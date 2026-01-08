# HTTP API Server

PowerMem HTTP API Server provides a production-ready RESTful API interface for PowerMem, enabling any application that supports HTTP calls to integrate PowerMem's intelligent memory capabilities.

## Overview

The PowerMem HTTP API Server is built with FastAPI and provides:

- **RESTful API endpoints** for all core PowerMem operations
- **API Key authentication** for secure access
- **Rate limiting** to protect server resources
- **Automatic API documentation** (Swagger UI and ReDoc)
- **Structured logging** with request tracking
- **CORS support** for web applications
- **Production-ready** deployment options

### Starting the API Server
```bash
# Method 1: Install from powermem package, use CLI command
pip install powermem
powermem-server --host 0.0.0.0 --port 8000

# Method 2: Using Docker
# Build and run with Docker
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

# Or use Docker Compose (recommended)
docker-compose -f docker/docker-compose.yml up -d

# Method 3: From source code, use Makefile
git clone git@github.com:oceanbase/powermem.git
cd powermem
# Start server (production mode)
make server-start

# Start server with auto-reload (development mode)
make server-start-reload

# Check server status
make server-status

# View server logs
make server-logs

# Stop server
make server-stop

# Restart server
make server-restart

```

### PowerMem .env Configuration
The PowerMem SDK configuration is the same as the previous v0.2.0 version, with the addition of PowerMem server configuration section 12. PowerMem HTTP API Server Configuration. In most cases, the default configuration can be kept.

```bash
=============================================================================
# 12. PowerMem HTTP API Server Configuration
# =============================================================================
# Configuration for the PowerMem HTTP API Server
# =============================================================================

# -----------------------------------------------------------------------------
# Server Settings
# -----------------------------------------------------------------------------
# Server host address (0.0.0.0 to listen on all interfaces)
POWERMEM_SERVER_HOST=0.0.0.0

# Server port number
POWERMEM_SERVER_PORT=8000

# Number of worker processes (only used when reload=false)
POWERMEM_SERVER_WORKERS=4

# Enable auto-reload for development (true/false)
POWERMEM_SERVER_RELOAD=false

# -----------------------------------------------------------------------------
# Authentication Settings
# -----------------------------------------------------------------------------
# Enable API key authentication (true/false)
POWERMEM_SERVER_AUTH_ENABLED=false

# API keys (comma-separated list)
# Example: POWERMEM_SERVER_API_KEYS=key1,key2,key3
POWERMEM_SERVER_API_KEYS=

# -----------------------------------------------------------------------------
# Rate Limiting Settings
# -----------------------------------------------------------------------------
# Enable rate limiting (true/false)
POWERMEM_SERVER_RATE_LIMIT_ENABLED=true

# Rate limit per minute per IP address
POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE=100

# -----------------------------------------------------------------------------
# Logging Settings
# -----------------------------------------------------------------------------
POWERMEM_SERVER_LOG_FILE=server.log

# Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
POWERMEM_SERVER_LOG_LEVEL=INFO

# Log format: json or text
POWERMEM_SERVER_LOG_FORMAT=text

# -----------------------------------------------------------------------------
# API Settings
# -----------------------------------------------------------------------------
# API title (shown in Swagger UI)
POWERMEM_SERVER_API_TITLE=PowerMem API

# API version
POWERMEM_SERVER_API_VERSION=v1

# API description (shown in Swagger UI)
POWERMEM_SERVER_API_DESCRIPTION=PowerMem HTTP API Server - Intelligent Memory System

# -----------------------------------------------------------------------------
# CORS Settings
# -----------------------------------------------------------------------------
# Enable CORS (true/false)
POWERMEM_SERVER_CORS_ENABLED=true

# CORS allowed origins (comma-separated, use * for all origins)
# Example: POWERMEM_SERVER_CORS_ORIGINS=http://localhost:3000,https://example.com
POWERMEM_SERVER_CORS_ORIGINS=*

```

### Available Tools
You can use the following tools to interact with the API:

+ **curl**: Command-line tool
+ **Postman**: GUI tool
+ **Swagger UI**: Access via browser at `http://0.0.0.0:8000/docs`

### Base URL
```plain
Base URL: http://0.0.0.0:8000
API Base: http://0.0.0.0:8000/api/v1
```

---

## Authentication
When authentication is enabled, configure the `.env` file:

```bash
# Enable API key authentication (true/false)
POWERMEM_AUTH_ENABLED=true

# API keys (comma-separated list)
# Example: POWERMEM_API_KEYS=key1,key2,key3
POWERMEM_API_KEYS=test-api-key-123
```

All endpoints that require authentication must include the API Key in the request header:

```bash
X-API-Key: test-api-key-123
```

**Exception**: The `/api/v1/system/health` endpoint is public and does not require authentication.

---

## System Endpoints
### Health Check
**Endpoint**: `GET /api/v1/system/health`

**Description**: Check the health status of the API server (public endpoint, no authentication required)

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/system/health"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "status": "healthy",
        "timestamp": "2025-12-24T07:10:06.455901Z"
    },
    "message": "Service is healthy",
    "timestamp": "2025-12-24T07:10:06.456033Z"
}
```

**Usage Notes**:

| Scenario | Expected Result |
| --- | --- |
| Normal request | Returns 200, status is "healthy" |
| No authentication | API Key not required |


---

### System Status
**Endpoint**: `GET /api/v1/system/status`

**Description**: Get system status and configuration information

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/system/status" -i

curl -X GET "http://localhost:8000/api/v1/system/status" \
  -H "X-API-Key: test-api-key-123" -i
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "status": "operational",
        "version": "v1",
        "storage_type": "oceanbase",
        "llm_provider": "qwen",
        "timestamp": "2025-12-24T07:37:20.316941Z"
    },
    "message": "System status retrieved successfully",
    "timestamp": "2025-12-24T07:37:20.317057Z"
}
```

**Usage Notes**:

| Scenario | Expected Result |
| --- | --- |
| Normal request | Returns 200 with system information |
| No API Key | Returns 401 Unauthorized |
| Invalid API Key | Returns 401 Unauthorized |


---

### System Metrics
**Endpoint**: `GET /api/v1/system/metrics`

**Description**: Get metrics in Prometheus format (placeholder implementation)

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/system/metrics" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
# HELP powermem_api_requests_total Total number of API requests
# TYPE powermem_api_requests_total counter
powermem_api_requests_total{method="GET",endpoint="/api/v1/system/status",status="200"} 1

# HELP powermem_memory_operations_total Total number of memory operations
# TYPE powermem_memory_operations_total counter

# HELP powermem_api_request_duration_seconds API request duration in seconds
# TYPE powermem_api_request_duration_seconds histogram
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="0.01"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="0.05"} 1
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="0.1"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="0.2"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="0.5"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="1.0"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="2.5"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="5.0"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="10.0"} 0
powermem_api_request_duration_seconds_bucket{method="GET",endpoint="/api/v1/system/status",le="+Inf"} 0
powermem_api_request_duration_seconds_sum{method="GET",endpoint="/api/v1/system/status"} 0.017825
powermem_api_request_duration_seconds_count{method="GET",endpoint="/api/v1/system/status"} 1

# HELP powermem_errors_total Total number of errors
# TYPE powermem_errors_total counter
```



### Delete All Memories
**Endpoint**: `DELETE /api/v1/system/delete-all-memories`

**Description**: Delete all memories

**Request Example**:

```bash
# Delete all memories (system level)
curl -X DELETE "http://localhost:8000/api/v1/system/delete-all-memories" \
  -H "X-API-Key: test-api-key-123"

# Delete all memories for a specific agent
curl -X DELETE "http://localhost:8000/api/v1/system/delete-all-memories?agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# Delete all memories for a specific user
curl -X DELETE "http://localhost:8000/api/v1/system/delete-all-memories?user_id=user-123" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
  "success": true,
  "data": {},
  "message": "All memories reset successfully",
  "timestamp": "2025-12-24T08:24:29.170996Z"
}
```

**Usage Notes**:

| Scenario | Expected Result |
| --- | --- |
| Normal deletion | Returns 200, all memories deleted |
| Query after deletion | Returns empty list |

## Memory Management Endpoints
### Create Memory
**Endpoint**: `POST /api/v1/memories`

**Description**: Create a new memory

**Request Example**:

```bash
curl -X POST "http://localhost:8000/api/v1/memories" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "User likes coffee and goes to Starbucks every morning",
    "user_id": "user-123",
    "agent_id": "agent-456",
    "run_id": "run-789",
    "metadata": {
      "source": "conversation",
      "importance": "high"
    },
    "filters": {
      "category": "preference",
      "topic": "beverage"
    },
    "scope": "user",
    "memory_type": "preference",
    "infer": true
  }'
```

```json
{
    "success": true,
    "data": [
        {
            "memory_id": 658954684471443456,
            "content": "User likes coffee",
            "user_id": "user-123",
            "agent_id": "agent-456",
            "run_id": "run-789",
            "metadata": {
                "source": "conversation",
                "importance": "high"
            }
        },
        {
            "memory_id": 658954684538552320,
            "content": "Goes to Starbucks every morning",
            "user_id": "user-123",
            "agent_id": "agent-456",
            "run_id": "run-789",
            "metadata": {
                "source": "conversation",
                "importance": "high"
            }
        }
    ],
    "message": "Created 2 memories successfully",
    "timestamp": "2025-12-24T08:50:39.586609Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Minimum parameters | Only content | Returns 200, creation successful |
| Full parameters | All fields | Returns 200, all fields saved |
| Missing content | No content field | Returns 422 Validation Error |
| Empty content | content is empty string | Returns 422 Validation Error |
| Invalid metadata | metadata format error | Returns 422 Validation Error |


---

### Batch Create Memories
**Endpoint**: `POST /api/v1/memories/batch`

**Description**: Create multiple memories in batch

**Request Example**:

```bash
curl -X POST "http://localhost:8000/api/v1/memories/batch" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "memories": [
      {
        "content": "User likes Python programming",
        "metadata": {"topic": "programming"},
        "filters": {"category": "skill"},
        "scope": "user",
        "memory_type": "skill"
      },
      {
        "content": "User lives in Beijing",
        "metadata": {"topic": "location"},
        "filters": {"category": "personal"},
        "scope": "user",
        "memory_type": "fact"
      }
    ],
    "user_id": "user-123",
    "agent_id": "agent-456",
    "run_id": "run-789",
    "infer": true
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memories": [
            {
                "memory_id": 658958021480677376,
                "content": "User likes Python programming",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "programming"
                },
                "created_at": "2025-12-24T09:03:55.157320Z",
                "updated_at": "2025-12-24T09:03:55.157330Z"
            },
            {
                "memory_id": 658958031962243072,
                "content": "User lives in Beijing",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "location"
                },
                "created_at": "2025-12-24T09:03:57.668669Z",
                "updated_at": "2025-12-24T09:03:57.668677Z"
            }
        ],
        "total": 2,
        "created_count": 2,
        "failed_count": 0
    },
    "message": "Created 2 out of 2 memories",
    "timestamp": "2025-12-24T09:03:57.738674Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal batch creation | 2-10 memories | Returns 200, all created successfully |
| Partial failure | Contains invalid data | Returns 200, failed_count > 0 |
| Empty list | memories is empty | Returns 422 Validation Error |
| Exceeds limit | > 100 memories | Returns 422 Validation Error |
| Mixed success/failure | Some valid, some invalid | Returns 200, shows success and failure counts |


---

### List Memories
**Endpoint**: `GET /api/v1/memories`

**Description**: Get a list of memories with pagination and filtering support

**Request Example**:

```bash
# Basic query
curl -X GET "http://localhost:8000/api/v1/memories?limit=10&offset=0" \
  -H "X-API-Key: test-api-key-123"

# Filter by user
curl -X GET "http://localhost:8000/api/v1/memories?user_id=user-123&limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"

# Filter by agent
curl -X GET "http://localhost:8000/api/v1/memories?agent_id=agent-456&limit=50&offset=0" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memories": [
            {
                "memory_id": 658958021480677376,
                "content": "User likes Python programming",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "programming"
                },
                "created_at": "2025-12-24T09:03:55.157320Z",
                "updated_at": "2025-12-24T09:03:57.682036Z"
            },
            {
                "memory_id": 658958031962243072,
                "content": "User lives in Beijing",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "location"
                },
                "created_at": "2025-12-24T09:03:57.668669Z",
                "updated_at": "2025-12-24T09:03:57.717598Z"
            }
        ],
        "total": 2,
        "limit": 10,
        "offset": 0
    },
    "message": "Memories retrieved successfully",
    "timestamp": "2025-12-24T09:08:17.639957Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Default pagination | No parameters | Returns limit=100, offset=0 |
| Custom pagination | limit=20, offset=10 | Returns 20 items, skips first 10 |
| Filter by user | user_id=user-123 | Returns only memories for that user |
| Filter by agent | agent_id=agent-456 | Returns only memories for that agent |
| Combined filters | user_id + agent_id | Returns records matching both |
| Limit exceeds max | limit=2000 | Returns 422 Validation Error |
| Negative offset | offset=-1 | Returns 422 Validation Error |
| Empty results | No matching records | Returns empty array |


---

### Get Memory
**Endpoint**: `GET /api/v1/memories/{memory_id}`

**Description**: Get a single memory by ID

**Request Example**:

```bash
# First, list all memories to see available IDs
curl -X GET "http://localhost:8000/api/v1/memories?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# Then query by specific ID
curl -X GET "http://localhost:8000/api/v1/memories/1?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memories": [
            {
                "memory_id": 658958021480677376,
                "content": "User likes Python programming",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "programming"
                },
                "created_at": "2025-12-24T09:03:55.157320Z",
                "updated_at": "2025-12-24T09:25:06.810068Z"
            },
            {
                "memory_id": 658958031962243072,
                "content": "User lives in Beijing",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "location"
                },
                "created_at": "2025-12-24T09:03:57.668669Z",
                "updated_at": "2025-12-24T09:03:57.717598Z"
            }
        ],
        "total": 2,
        "limit": 100,
        "offset": 0
    },
    "message": "Memories retrieved successfully",
    "timestamp": "2025-12-24T09:25:21.217493Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal retrieval | Existing memory_id | Returns 200 with complete memory information |
| Non-existent ID | memory_id=99999 | Returns 404 Not Found |
| Invalid ID | memory_id=abc | Returns 422 Validation Error |
| Access control | Wrong user_id | Returns 403 or 404 |
| Access control | Wrong agent_id | Returns 403 or 404 |


---

### Update Memory
**Endpoint**: `PUT /api/v1/memories/{memory_id}`

**Description**: Update an existing memory

**Request Example**:

```bash
# First, list all memories to see available IDs
curl -X GET "http://localhost:8000/api/v1/memories?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# Update content
curl -X PUT "http://localhost:8000/api/v1/memories/658958031962243072" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "User likes latte coffee and goes to Starbucks every morning",
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'

# Update metadata
curl -X PUT "http://localhost:8000/api/v1/memories/658958031962243072" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {
      "source": "conversation",
      "importance": "high",
      "updated_by": "admin"
    },
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'

# Update both content and metadata
curl -X PUT "http://localhost:8000/api/v1/memories/658958031962243072" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "User likes latte coffee",
    "metadata": {
      "importance": "medium"
    },
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memory_id": 658958031962243072,
        "content": "User likes latte coffee",
        "user_id": "user-123",
        "agent_id": "agent-456",
        "run_id": "run-789",
        "metadata": {
            "topic": "location",
            "source": "conversation",
            "importance": "medium",
            "updated_by": "admin",
            "memory_type": "working",
            "access_count": 0,
            "intelligence": {
                "importance_score": 0,
                "memory_type": "working",
                "initial_retention": 0,
                "decay_rate": 0.2,
                "current_retention": 0,
                "next_review": "2025-12-24T18:41:21.908824+08:00",
                "review_schedule": [
                    "2025-12-24T18:41:21.908824+08:00",
                    "2025-12-24T23:41:21.908824+08:00",
                    "2025-12-25T17:41:21.908824+08:00",
                    "2025-12-27T17:41:21.908824+08:00",
                    "2025-12-31T17:41:21.908824+08:00"
                ],
                "last_reviewed": "2025-12-24T17:41:21.908824+08:00",
                "review_count": 0,
                "access_count": 0,
                "reinforcement_factor": 0.3
            },
            "importance_score": 0,
            "memory_management": {
                "should_promote": false,
                "should_forget": false,
                "should_archive": false,
                "is_active": true
            },
            "processing_applied": true
        },
        "created_at": "2025-12-24T09:03:57.668669Z",
        "updated_at": "2025-12-24T09:41:21.908991Z"
    },
    "message": "Memory updated successfully",
    "timestamp": "2025-12-24T09:41:21.930404Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Update content | Only content | Returns 200, content updated |
| Update metadata | Only metadata | Returns 200, metadata updated |
| Update both | content + metadata | Returns 200, both updated |
| No update fields | Both content and metadata are empty | Returns 400 Error |
| Non-existent ID | memory_id=99999 | Returns 404 Not Found |
| Access control | Wrong user_id | Returns 403 or 404 |


---

### Batch Update Memories
**Endpoint**: `PUT /api/v1/memories/batch`

**Description**: Update multiple memories in batch

**Request Example**:

```bash
curl -X PUT "http://localhost:8000/api/v1/memories/batch" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "updates": [
      {
        "memory_id": 658958021480677376,
        "content": "Updated content 1",
        "metadata": {"updated": true}
      },
      {
        "memory_id": 658958031962243072,
        "metadata": {"updated": true}
      }
    ],
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memories": [
            {
                "memory_id": 658958021480677376,
                "content": "Updated content 1",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "programming",
                    "updated": true,
                    "memory_type": "working",
                    "access_count": 0,
                    "intelligence": {
                        "decay_rate": 0.2,
                        "memory_type": "working",
                        "next_review": "2025-12-24T18:44:57.696937+08:00",
                        "access_count": 0,
                        "review_count": 0,
                        "last_reviewed": "2025-12-24T17:44:57.696937+08:00",
                        "review_schedule": [
                            "2025-12-24T18:44:57.696937+08:00",
                            "2025-12-24T23:44:57.696937+08:00",
                            "2025-12-25T17:44:57.696937+08:00",
                            "2025-12-27T17:44:57.696937+08:00",
                            "2025-12-31T17:44:57.696937+08:00"
                        ],
                        "importance_score": 0,
                        "current_retention": 0,
                        "initial_retention": 0,
                        "reinforcement_factor": 0.3
                    },
                    "importance_score": 0,
                    "memory_management": {
                        "is_active": true,
                        "should_forget": false,
                        "should_archive": false,
                        "should_promote": false
                    },
                    "processing_applied": true
                },
                "created_at": "2025-12-24T09:03:55.157320Z",
                "updated_at": "2025-12-24T09:44:57.697059Z"
            },
            {
                "memory_id": 658958031962243072,
                "content": "User likes latte coffee",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "topic": "location",
                    "source": "conversation",
                    "updated": true,
                    "importance": "medium",
                    "updated_by": "admin",
                    "memory_type": "working",
                    "access_count": 0,
                    "intelligence": {
                        "decay_rate": 0.2,
                        "memory_type": "working",
                        "next_review": "2025-12-24T18:44:58.114457+08:00",
                        "access_count": 0,
                        "review_count": 0,
                        "last_reviewed": "2025-12-24T17:44:58.114457+08:00",
                        "review_schedule": [
                            "2025-12-24T18:44:58.114457+08:00",
                            "2025-12-24T23:44:58.114457+08:00",
                            "2025-12-25T17:44:58.114457+08:00",
                            "2025-12-27T17:44:58.114457+08:00",
                            "2025-12-31T17:44:58.114457+08:00"
                        ],
                        "importance_score": 0,
                        "current_retention": 0,
                        "initial_retention": 0,
                        "reinforcement_factor": 0.3
                    },
                    "importance_score": 0,
                    "memory_management": {
                        "is_active": true,
                        "should_forget": false,
                        "should_archive": false,
                        "should_promote": false
                    },
                    "processing_applied": true
                },
                "created_at": "2025-12-24T09:03:57.668669Z",
                "updated_at": "2025-12-24T09:44:58.114565Z"
            }
        ],
        "total": 2,
        "updated_count": 2,
        "failed_count": 0
    },
    "message": "Updated 2 out of 2 memories",
    "timestamp": "2025-12-24T09:44:58.180191Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal batch update | 2-10 updates | Returns 200, all updated successfully |
| Partial failure | Contains non-existent IDs | Returns 200, failed_count > 0 |
| Empty list | updates is empty | Returns 422 Validation Error |
| Exceeds limit | > 100 updates | Returns 422 Validation Error |


---

### Delete Memory
**Endpoint**: `DELETE /api/v1/memories/{memory_id}`

**Description**: Delete a single memory

**Request Example**:

```bash
curl -X DELETE "http://localhost:8000/api/v1/memories/658958021480677376?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memory_id": 658958021480677376
    },
    "message": "Memory deleted successfully",
    "timestamp": "2025-12-24T09:45:47.174799Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal deletion | Existing memory_id | Returns 200, deletion successful |
| Non-existent ID | memory_id=99999 | Returns 404 Not Found |
| Query after deletion | Query same ID again | Returns 404 Not Found |
| Access control | Wrong user_id | Returns 403 or 404 |


---

### Bulk Delete Memories
**Endpoint**: `DELETE /api/v1/memories/batch`

**Description**: Delete multiple memories in batch

**Request Example**:

```bash
curl -X DELETE "http://localhost:8000/api/v1/memories/batch" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_ids": [658958031962243072, 658968835172335616, 658968835277193216],
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "deleted": [
            658958031962243072,
            658968835172335616,
            658968835277193216
        ],
        "failed": [

        ],
        "total": 3,
        "deleted_count": 3,
        "failed_count": 0
    },
    "message": "Deleted 3 memories",
    "timestamp": "2025-12-24T09:47:56.022512Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal bulk deletion | 5-10 IDs | Returns 200, all deleted successfully |
| Partial failure | Contains non-existent IDs | Returns 200, failed_count > 0 |
| Empty list | memory_ids is empty | Returns 422 Validation Error |
| Exceeds limit | > 100 IDs | Returns 422 Validation Error |


---

## Search Endpoints
### Search Memories (POST)
**Endpoint**: `POST /api/v1/memories/search`

**Description**: Search memories using semantic search (supports complex filtering)

**Request Example**:

```bash
# First, create some data
curl -X POST "http://localhost:8000/api/v1/memories"   -H "X-API-Key: test-api-key-123"   -H "Content-Type: application/json"   -d '{
    "content": "User likes coffee and goes to Starbucks every morning",
    "user_id": "user-123",
    "agent_id": "agent-456",
    "run_id": "run-789",
    "metadata": {
      "source": "conversation",
      "importance": "high"
    },
    "filters": {
      "category": "preference",
      "topic": "beverage"
    },
    "scope": "user",
    "memory_type": "preference",
    "infer": true
  }'

# Search
curl -X POST "http://localhost:8000/api/v1/memories/search" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does the user like to drink",
    "user_id": "user-123",
    "agent_id": "agent-456",
    "run_id": "run-789",
    "filters": {
      "category": "preference",
      "topic": "beverage"
    },
    "limit": 10
  }'
```

**Response Example**:

```json
{
  "success": true,
  "data": {
    "results": [
      {
        "memory_id": 1,
        "content": "User likes coffee and goes to Starbucks every morning",
        "score": 0.95,
        "metadata": {
          "source": "conversation",
          "importance": "high"
        }
      },
      {
        "memory_id": 5,
        "content": "User occasionally drinks tea",
        "score": 0.78,
        "metadata": {}
      }
    ],
    "total": 2,
    "query": "What beverages does the user like"
  },
  "message": "Search completed successfully",
  "timestamp": "2024-01-15T11:00:00Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Basic search | Only query | Returns relevant memories, sorted by relevance |
| With user filter | query + user_id | Returns only memories for that user |
| With agent filter | query + agent_id | Returns only memories for that agent |
| With complex filters | query + filters | Returns memories matching filter conditions |
| Limit results | limit=5 | Returns at most 5 results |
| Empty query | query is empty | Returns 422 Validation Error |
| No results | No matching memories | Returns empty array |
| Limit exceeds max | limit=200 | Returns 422 Validation Error |


---

### Search Memories (GET)
**Endpoint**: `POST /api/v1/memories/search`

**Description**: Search using query parameters

**Request Example**:

```bash
curl -X POST "http://localhost:8000/api/v1/memories/search" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What beverages does the user like",
    "user_id": "user-123",
    "agent_id": "agent-456",
    "limit": 10
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "results": [
            {
                "memory_id": 658969617326145536,
                "content": "Likes coffee",
                "score": 0.01639344262295082,
                "metadata": {
                    "source": "conversation",
                    "importance": "high",
                    "_fusion_info": {
                        "fts_rank": 1,
                        "rrf_score": 0.01639344262295082,
                        "fts_weight": 0.5,
                        "vector_rank": 1,
                        "fusion_method": "rrf",
                        "vector_weight": 0.5
                    },
                    "search_count": 2,
                    "last_searched_at": "2025-12-24T18:23:45.620404+08:00"
                }
            },
            {
                "memory_id": 658969617401643008,
                "content": "Goes to Starbucks every morning",
                "score": 0.008064516129032258,
                "metadata": {
                    "source": "conversation",
                    "importance": "high",
                    "_fusion_info": {
                        "fts_rank": null,
                        "rrf_score": 0.008064516129032258,
                        "fts_weight": 0.5,
                        "vector_rank": 2,
                        "fusion_method": "rrf",
                        "vector_weight": 0.5
                    },
                    "search_count": 2,
                    "last_searched_at": "2025-12-24T18:23:45.620435+08:00"
                }
            }
        ],
        "total": 2,
        "query": "What beverages does the user like"
    },
    "message": "Search completed successfully",
    "timestamp": "2025-12-24T10:23:45.659143Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Basic search | query parameter | Returns relevant memories |
| With filters | query + user_id + agent_id | Returns matching memories |
| Missing query | No query parameter | Returns 422 Validation Error |
| URL encoding | Non-ASCII query | Properly handles URL encoding |


---

## User Profile Endpoints
### Update User Profile
**Endpoint**: `POST /api/v1/users/{user_id}/profile`

**Description**: Generate or update a user profile

**Request Example**:

```bash
curl -X POST "http://localhost:8000/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_content": "User is a senior software engineer, focused on AI and machine learning",
    "topics": {
      "programming": ["Python", "JavaScript", "Go"],
      "interests": ["Machine Learning", "Deep Learning", "NLP"],
      "location": "Beijing"
    }
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "user_id": "user-123",
        "profile_content": "User is a senior software engineer, focused on AI and machine learning",
        "topics": {
            "location": "Beijing",
            "interests": [
                "Machine Learning",
                "Deep Learning",
                "NLP"
            ],
            "programming": [
                "Python",
                "JavaScript",
                "Go"
            ]
        },
        "updated_at": "2025-12-24T10:31:13.169725Z"
    },
    "message": "User profile updated successfully",
    "timestamp": "2025-12-24T10:31:13.195518Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Update content | Only profile_content | Returns 200, content updated |
| Update topics | Only topics | Returns 200, topics updated |
| Update both | profile_content + topics | Returns 200, both updated |
| Partial update | Only update some topics | Returns 200, merged update |
| Empty content | profile_content is empty | Returns 200, content cleared |


---

### Get User Profile
**Endpoint**: `GET /api/v1/users/{user_id}/profile`

**Description**: Get the profile of a specific user

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "user_id": "user-123",
        "profile_content": "User is a senior software engineer, focused on AI and machine learning",
        "topics": {
            "location": "Beijing",
            "interests": [
                "Machine Learning",
                "Deep Learning",
                "NLP"
            ],
            "programming": [
                "Python",
                "JavaScript",
                "Go"
            ]
        },
        "updated_at": "2025-12-24T10:31:13.169725Z"
    },
    "message": "User profile retrieved successfully",
    "timestamp": "2025-12-24T10:32:53.838365Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal retrieval | Existing user_id | Returns 200 with user profile |
| Non-existent user | user_id=unknown | Returns 404 or empty profile |
| User without profile | New user | Returns empty profile_content |


---

### Delete User Profile
**Endpoint**: `DELETE /api/v1/users/{user_id}/profile`

**Description**: Delete the user profile for a specific user

**Request Example**:

```bash
curl -X DELETE "http://localhost:8000/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "user_id": "user-123",
        "deleted": true
    },
    "message": "User profile for user-123 deleted successfully",
    "timestamp": "2025-12-24T10:45:30.123456Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal deletion | Existing user_id with profile | Returns 200, profile deleted successfully |
| Non-existent user | user_id=unknown | Returns 404 Not Found |
| User without profile | User has no profile | Returns 404 Not Found |
| Query after deletion | Query same user again | Returns 404 Not Found |


---

### Get User Memories
**Endpoint**: `GET /api/v1/users/{user_id}/memories`

**Description**: Get all memories for a specific user

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/users/user-123/memories?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memories": [
            {
                "memory_id": 658969617326145536,
                "content": "Likes coffee",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "source": "conversation",
                    "importance": "high",
                    "_fusion_info": {
                        "fts_rank": 1,
                        "rrf_score": 0.01639344262295082,
                        "fts_weight": 0.5,
                        "vector_rank": 1,
                        "fusion_method": "rrf",
                        "vector_weight": 0.5
                    },
                    "search_count": 2,
                    "last_searched_at": "2025-12-24T18:23:45.620404+08:00"
                },
                "created_at": "2025-12-24T09:49:59.822334Z",
                "updated_at": "2025-12-24T10:23:45.620371Z"
            },
            {
                "memory_id": 658969617401643008,
                "content": "Goes to Starbucks every morning",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789",
                "metadata": {
                    "source": "conversation",
                    "importance": "high",
                    "_fusion_info": {
                        "fts_rank": null,
                        "rrf_score": 0.008064516129032258,
                        "fts_weight": 0.5,
                        "vector_rank": 2,
                        "fusion_method": "rrf",
                        "vector_weight": 0.5
                    },
                    "search_count": 2,
                    "last_searched_at": "2025-12-24T18:23:45.620435+08:00"
                },
                "created_at": "2025-12-24T09:49:59.852494Z",
                "updated_at": "2025-12-24T10:23:45.620411Z"
            }
        ],
        "total": 2,
        "limit": 20,
        "offset": 0
    },
    "message": "User memories retrieved successfully",
    "timestamp": "2025-12-24T10:35:40.820301Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal retrieval | Existing user_id | Returns all memories for that user |
| Paginated query | limit=10, offset=10 | Returns paginated results |
| User without memories | New user | Returns empty array |
| Non-existent user | user_id=unknown | Returns empty array or 404 |


---

### Delete User Memories
**Endpoint**: `DELETE /api/v1/users/{user_id}/memories`

**Description**: Delete all memories for a specific user (user profile deletion)

**Request Example**:

```bash
curl -X DELETE "http://localhost:8000/api/v1/users/user-123/memories" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "user_id": "user-123",
        "deleted_count": 2,
        "failed_count": 0,
        "total": 2
    },
    "message": "Deleted 2 memories for user user-123",
    "timestamp": "2025-12-24T10:39:15.125245Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal deletion | Existing user_id | Returns 200, all memories deleted |
| User without memories | New user | Returns deleted_count=0 |
| Query after deletion | Query same user again | Returns empty array |


---

## Agent Management Endpoints

### Create Agent Memory
**Endpoint**: `POST /api/v1/agents/{agent_id}/memories`

**Description**: Create a memory for a specific agent

**Request Example**:

```bash
curl -X POST "http://localhost:8000/api/v1/agents/agent-456/memories"   -H "X-API-Key: test-api-key-123"   -H "Content-Type: application/json"   -d '{
    "content": "Agent learned new conversation techniques",
    "user_id": "user-123",
    "run_id": "run-789"
  }'
```

**Response Example**:

```json
{
    "success": true,
    "data": {
        "memory_id": 659015038446600192,
        "content": "Agent learned new conversation techniques",
        "user_id": null,
        "agent_id": "agent-456",
        "run_id": null,
        "metadata": {
            "run_id": "run-789",
            "agent": {
                "agent_id": "agent-456",
                "mode": "multi_agent",
                "scope": "private",
                "collaboration": {
                    "is_collaborating": false,
                    "collaboration_type": null,
                    "collaboration_status": null,
                    "participants": [

                    ],
                    "collaboration_level": "low"
                },
                "permissions": {
                    "scope_permissions": {
                        "read": true,
                        "write": true,
                        "delete": true,
                        "admin": false
                    },
                    "scope_type": "private",
                    "access_level": "owner"
                },
                "sharing": {
                    "is_shared": false,
                    "shared_with": [

                    ],
                    "sharing_level": "private",
                    "can_share": true
                }
            },
            "intelligence": {
                "importance_score": 0.65,
                "memory_type": "short_term",
                "initial_retention": 0.65,
                "decay_rate": 0.15000000000000002,
                "current_retention": 0.65,
                "next_review": "2025-12-24T21:38:46.649257+08:00",
                "review_schedule": [
                    "2025-12-24T21:38:46.649257+08:00",
                    "2025-12-25T01:40:16.649257+08:00",
                    "2025-12-25T16:09:40.649257+08:00",
                    "2025-12-27T06:48:04.649257+08:00",
                    "2025-12-30T12:04:52.649257+08:00"
                ],
                "last_reviewed": "2025-12-24T20:50:28.649257+08:00",
                "review_count": 0,
                "access_count": 0,
                "reinforcement_factor": 0.3
            },
            "memory_management": {
                "should_promote": false,
                "should_forget": false,
                "should_archive": false,
                "is_active": true
            },
            "created_at": "2025-12-24T20:50:28.649257+08:00",
            "updated_at": "2025-12-24T20:50:28.649257+08:00"
        },
        "created_at": "2025-12-24T20:50:29.556144Z",
        "updated_at": null
    },
    "message": "Agent memory created successfully",
    "timestamp": "2025-12-24T12:50:29.556662Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal creation | content parameter | Returns 200, creation successful |
| With user ID | content + user_id | Returns 200, associated with user |
| With run ID | content + run_id | Returns 200, associated with run |
| Missing content | No content | Returns 422 Validation Error |


---

### Get Agent Memories
**Endpoint**: `GET /api/v1/agents/{agent_id}/memories`

**Description**: Get all memories for a specific agent

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/agents/agent-456/memories?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
  "success": true,
  "data": {
    "memories": [
      {
        "memory_id": 2,
        "content": "Agent learned to handle user preferences",
        "user_id": "user-123",
        "agent_id": "agent-456",
        "metadata": {},
        "created_at": "2024-01-15T10:30:00Z"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  },
  "message": "Agent memories retrieved successfully",
  "timestamp": "2024-01-15T11:30:00Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal retrieval | Existing agent_id | Returns all memories for that agent |
| Paginated query | limit=10, offset=10 | Returns paginated results |
| Agent without memories | New agent | Returns empty array |


---

### Share Agent Memories
**Endpoint**: `POST /api/v1/agents/{agent_id}/memories/share`

**Description**: Share agent memories with another agent

**Request Example**:

```bash
# Share all memories
curl -X POST "http://localhost:8000/api/v1/agents/agent-456/memories/share" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "target_agent_id": "agent-789"
  }'

# Share specific memories
curl -X POST "http://localhost:8000/api/v1/agents/agent-456/memories/share" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "target_agent_id": "agent-789",
    "memory_ids": [1, 2, 3]
  }'
```

**Response Example**:

```json
{
  "success": true,
  "data": {
    "shared_count": 3,
    "source_agent_id": "agent-456",
    "target_agent_id": "agent-789"
  },
  "message": "Shared 3 memories successfully",
  "timestamp": "2024-01-15T11:40:00Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Share all memories | Only target_agent_id | Returns 200, all memories shared |
| Share specific memories | target_agent_id + memory_ids | Returns 200, specified memories shared |
| Non-existent memory IDs | Invalid memory_ids | Returns 200, shared_count less than requested |
| Non-existent target agent | target_agent_id=unknown | Returns 200, creates new agent record |


---

### Get Shared Memories
**Endpoint**: `GET /api/v1/agents/{agent_id}/memories/share`

**Description**: Get shared memories received by an agent

**Request Example**:

```bash
curl -X GET "http://localhost:8000/api/v1/agents/agent-789/memories/share?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```

**Response Example**:

```json
{
  "success": true,
  "data": {
    "memories": [
      {
        "memory_id": 1,
        "content": "Shared memory content",
        "user_id": "user-123",
        "agent_id": "agent-456",
        "metadata": {
          "shared_from": "agent-456"
        },
        "created_at": "2024-01-15T10:30:00Z"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  },
  "message": "Shared memories retrieved successfully",
  "timestamp": "2024-01-15T11:45:00Z"
}
```

**Usage Notes**:

| Scenario | Request Parameters | Expected Result |
| --- | --- | --- |
| Normal retrieval | Existing agent_id | Returns list of shared memories |
| No shared memories | New agent | Returns empty array |
| Paginated query | limit=10, offset=10 | Returns paginated results |


---

## Error Scenarios
### Authentication Errors
**Error Cases**:

| Scenario | Request | Expected Result |
| --- | --- | --- |
| No API Key | Missing X-API-Key header | Returns 401 Unauthorized |
| Invalid API Key | X-API-Key: invalid-key | Returns 401 Unauthorized |
| Empty API Key | X-API-Key: (empty) | Returns 401 Unauthorized |


**Example**:

```bash
# No API Key
curl -X GET "http://localhost:8000/api/v1/memories"

# Response
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "API key is required"
  }
}
```

---

### Rate Limiting
**Error Cases**:

| Scenario | Request | Expected Result |
| --- | --- | --- |
| Normal request | Single request | Returns 200 |
| Rate limit exceeded | Many requests in short time | Returns 429 Too Many Requests |


**Example**:

```bash
# Send 200 requests quickly
for i in {1..200}; do
  curl -X GET "http://localhost:8000/api/v1/memories" \
    -H "X-API-Key: test-api-key-123" &
done

# Response (when rate limit exceeded)
{
  "success": false,
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded: 100 per minute"
  }
}
```

---

### Validation Errors
**Error Cases**:

| Scenario | Request | Expected Result |
| --- | --- | --- |
| Missing required field | Missing content | Returns 422 Validation Error |
| Type error | memory_id="abc" | Returns 422 Validation Error |
| Range error | limit=2000 | Returns 422 Validation Error |
| Format error | Invalid JSON format | Returns 422 Validation Error |


**Example**:

```bash
# Missing required field
curl -X POST "http://localhost:8000/api/v1/memories" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-123"
  }'

# Response
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Field 'content' is required"
  }
}
```

---

### Resource Not Found
**Error Cases**:

| Scenario | Request | Expected Result |
| --- | --- | --- |
| Non-existent memory | GET /memories/99999 | Returns 404 Not Found |
| Non-existent user | GET /users/unknown/profile | Returns 404 or empty data |
| Non-existent agent | GET /agents/unknown/memories | Returns empty array |


---

### Server Errors
**Error Cases**:

| Scenario | Request | Expected Result |
| --- | --- | --- |
| Database connection failure | Any request | Returns 500 Internal Server Error |
| Service unavailable | Any request | Returns 503 Service Unavailable |


---

## Performance Testing
### Response Time Testing
Use tools to measure endpoint response times:

```bash
# Using curl to measure response time
time curl -X GET "http://localhost:8000/api/v1/memories" \
  -H "X-API-Key: test-api-key-123"

# Using httpie
http --timeout=5 GET "http://localhost:8000/api/v1/memories" \
  X-API-Key:test-api-key-123
```


---

### Concurrent Testing
Use tools for concurrent load testing:

```bash
# Using Apache Bench
ab -n 1000 -c 10 -H "X-API-Key: test-api-key-123" \
  http://localhost:8000/api/v1/memories

# Using wrk
wrk -t4 -c100 -d30s -H "X-API-Key: test-api-key-123" \
  http://localhost:8000/api/v1/memories
```

---
