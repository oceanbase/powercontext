# HTTP API 服务器 {#http-api-server}

PowerMem HTTP API 服务器为 PowerMem 提供了一个可用于生产环境的 RESTful API 接口，使任何支持 HTTP 调用的应用程序都能够集成 PowerMem 的智能记忆功能。

## 概述 {#overview}

PowerMem HTTP API 服务器基于 FastAPI 构建，提供以下功能：

- **RESTful API 端点**，支持所有核心 PowerMem 操作
- **API Key 认证**，确保安全访问
- **速率限制**，保护服务器资源
- **自动生成的 API 文档**（Swagger UI 和 ReDoc）
- **结构化日志记录**，支持请求追踪
- **CORS 支持**，适用于 Web 应用
- **生产环境就绪**的部署选项

### 启动 API 服务器 {#starting-the-api-server}

> **注意：** 如果您已经运行了 PowerMem 服务器，可以跳过此部分并继续下一步。
```bash
# 方法 1：从 powermem 包安装并使用 CLI 命令
pip install powermem
powermem-server --host 0.0.0.0 --port 8848

# 方法 2：使用 Docker Compose（推荐）
cp .env.example docker/.env
# 启动服务前，先编辑 docker/.env 并配置 LLM provider/key。
docker compose -f docker/docker-compose.yml up -d --build

# 方法 3：从源码使用 Makefile
git clone git@github.com:oceanbase/powermem.git
cd powermem
# 启动服务器（生产模式）
make server-start

# 启动带自动重载的服务器（开发模式）
make server-start-reload

# 检查服务器状态
make server-status

# 查看服务器日志
make server-logs

# 停止服务器
make server-stop

# 重启服务器
make server-restart

```
### 使用 Docker 部署 {#deploy-with-docker}

如果希望复用 `powermem-server` HTTP API，但不想在宿主机上安装 Python 依赖，可以使用 Docker。以下命令都在项目根目录执行。

#### 1. 准备配置 {#prepare-configuration}

```bash
cp .env.example .env
cp .env.example docker/.env
```

编辑 `docker/.env`，至少配置所选 LLM provider 需要的模型、API key 和 base URL。Compose 文件会通过 `env_file` 读取 `docker/.env`，用于加载应用侧配置。它会有意覆盖 `DATABASE_PROVIDER` 和 `OCEANBASE_*`，让 Server 使用内置的 seekdb 容器。如果还需要在本地运行 SDK 或单容器部署，可以继续保留项目根目录的 `.env`。

#### 2. 启动 PowerMem Server 与 seekdb {#start-powermem-server-with-seekdb}

推荐的本地部署方式会同时启动 `powermem-server` 和 `seekdb`：

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

如果 `.env` 已经指向现有数据库服务，请使用[单容器部署](#single-container-deployment)或自定义 Compose override。

如果需要为内置的 seekdb 设置 root 密码，启动时传入 `SEEKDB_ROOT_PASSWORD`：

```bash
SEEKDB_ROOT_PASSWORD=change-me docker compose -f docker/docker-compose.yml up -d --build
```

该部署会暴露以下入口：

| 服务 | 地址 / 端口 | 说明 |
| --- | --- | --- |
| PowerMem API | `http://localhost:8848` | REST API 与 Dashboard |
| 健康检查 | `http://localhost:8848/api/v1/system/health` | 公开端点 |
| Dashboard | `http://localhost:8848/dashboard/` | 记忆检查与监控 |
| seekdb MySQL | `localhost:2881` | 如设置了 `SEEKDB_ROOT_PASSWORD`，则 root 使用该密码 |
| seekdb 控制台 | `http://localhost:2886` | seekdb Web Dashboard |

#### 3. 验证、查看日志与停止 {#verify-inspect-and-stop}

```bash
curl http://localhost:8848/api/v1/system/health
docker compose -f docker/docker-compose.yml logs -f powermem-server
docker compose -f docker/docker-compose.yml down
```

只有在同时想删除持久化的 `docker_seekdb_data` Docker volume 时，才为 `down` 添加 `-v`：

```bash
docker compose -f docker/docker-compose.yml down -v
```

#### 单容器部署 {#single-container-deployment}

如果 `.env` 已经指向现有数据库服务，并且不希望启动内置 `seekdb` 容器，也可以只构建并运行 Server 镜像：

```bash
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
docker run -d \
  --name powermem-server \
  -p 8848:8848 \
  -v "$(pwd)/.env:/app/.env:ro" \
  --env-file .env \
  oceanbase/powermem-server:latest
```

中文快速部署说明见 [Docker 目录中文说明](https://github.com/oceanbase/powermem/blob/main/docker/README_CN.md)，完整镜像构建参数、镜像源配置和生产环境注意事项见 [Docker 部署说明](https://github.com/oceanbase/powermem/blob/main/docker/DOCKER.md)。

### Dashboard 浏览器行为 {#dashboard-browser-behavior}

当 `powermem-server` 从交互式本地终端启动且已构建的 Dashboard 资源可用时，它会等待 `/dashboard/` 可访问，并在默认浏览器中打开该页面。
```bash
# 禁用自动打开浏览器
powermem-server --no-open-browser

# 显式请求打开浏览器，包括输出被重定向的情况
powermem-server --open-browser
```
在 CI、容器、SSH 会话和无头环境中，总是会跳过浏览器的打开操作。绑定到 `0.0.0.0` 或 `::` 时，会打开等效的回环 URL，而不是未指定地址的 URL。

### 可选：构建仪表盘资源并同步到后端静态目录 {#optional-build-dashboard-assets-and-sync-to-backend-static-directory}

如果需要后端服务提供最新的仪表盘静态文件，请在启动或重启服务器之前运行以下步骤。如果您的部署中不需要前端资源，可以跳过此部分。
```bash
# 1）构建 Dashboard
cd dashboard
pnpm install
pnpm build
cd ..

# 2）同步前端产物到后端静态目录
mkdir -p src/server/dashboard
cp -r dashboard/dist/* src/server/dashboard/
```
一旦仪表板构建完成并且服务器正在运行，您可以通过 `http://localhost:8848/dashboard/` 访问它。有关仪表板功能和使用的完整指南，请参阅 [Web Dashboard Guide](../guides/0013-dashboard.md)。

### PowerMem .env 配置 {#powermem-env-configuration}
PowerMem SDK 的配置与之前的 v0.2.0 版本相同，新增了 PowerMem 服务器配置部分 12：PowerMem HTTP API Server Configuration。在大多数情况下，可以保留默认配置。
```bash
=============================================================================
# 12. PowerMem HTTP API Server 配置
# =============================================================================
# PowerMem HTTP API Server 配置
# =============================================================================

# -----------------------------------------------------------------------------
# 服务器设置
# -----------------------------------------------------------------------------
# 服务器主机地址（0.0.0.0 表示监听所有网卡）
POWERMEM_SERVER_HOST=0.0.0.0

# 服务器端口号
POWERMEM_SERVER_PORT=8848

# worker 进程数量（仅 reload=false 时使用）
POWERMEM_SERVER_WORKERS=4

# 开发环境启用自动重载（true/false）
POWERMEM_SERVER_RELOAD=false

# -----------------------------------------------------------------------------
# 认证设置
# -----------------------------------------------------------------------------
# 启用 API key 认证（true/false）
POWERMEM_SERVER_AUTH_ENABLED=false

# API key（逗号分隔列表）
# 示例：POWERMEM_SERVER_API_KEYS=key1,key2,key3
POWERMEM_SERVER_API_KEYS=

# -----------------------------------------------------------------------------
# 限流设置
# -----------------------------------------------------------------------------
# 启用限流（true/false）
POWERMEM_SERVER_RATE_LIMIT_ENABLED=true

# 每个 IP 地址每分钟的限流值
POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE=100

# -----------------------------------------------------------------------------
# 日志设置
# -----------------------------------------------------------------------------
POWERMEM_SERVER_LOG_FILE=server-output.txt

# 日志级别：DEBUG、INFO、WARNING、ERROR、CRITICAL
POWERMEM_SERVER_LOG_LEVEL=INFO

# 日志格式：json 或 text
POWERMEM_SERVER_LOG_FORMAT=text

# -----------------------------------------------------------------------------
# API 设置
# -----------------------------------------------------------------------------
# API 标题（显示在 Swagger UI 中）
POWERMEM_SERVER_API_TITLE=PowerMem API

# API 版本
POWERMEM_SERVER_API_VERSION=v1

# API 描述（显示在 Swagger UI 中）
POWERMEM_SERVER_API_DESCRIPTION=PowerMem HTTP API Server - Intelligent Memory System

# -----------------------------------------------------------------------------
# CORS 设置
# -----------------------------------------------------------------------------
# 启用 CORS（true/false）
POWERMEM_SERVER_CORS_ENABLED=true

# CORS 允许的 origin（逗号分隔，使用 * 表示所有 origin）
# 示例：POWERMEM_SERVER_CORS_ORIGINS=http://localhost:3000,https://example.com
POWERMEM_SERVER_CORS_ORIGINS=*

```
### 可用工具 {#available-tools}
您可以使用以下工具与 API 交互：

+ **curl**: 命令行工具
+ **Postman**: 图形界面工具
+ **Swagger UI**: 通过浏览器访问 `http://0.0.0.0:8848/docs`

### 基础 URL {#base-url}
```plain
Base URL: http://0.0.0.0:8848
API Base: http://0.0.0.0:8848/api/v1
```
---

## 身份验证 {#authentication}
当启用身份验证时，请配置 `.env` 文件：
```bash
# 启用 API key 认证（true/false）
POWERMEM_AUTH_ENABLED=true

# API key（逗号分隔列表）
# 示例：POWERMEM_API_KEYS=key1,key2,key3
POWERMEM_API_KEYS=test-api-key-123
```
所有需要身份验证的端点必须在请求头中包含 API Key：
```bash
X-API-Key: test-api-key-123
```
**异常**: `/api/v1/system/health` 端点是公开的，不需要认证。

---

## 系统端点 {#system-endpoints}
### 健康检查 {#health-check}
**端点**: `GET /api/v1/system/health`

**描述**: 检查 API 服务器的健康状态（公开端点，无需认证）

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/system/health"
```
**响应示例**:
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
**使用说明**:

| 场景 | 预期结果 |
| --- | --- |
| 正常请求 | 返回 200，状态为 "healthy" |
| 无认证 | 不需要 API Key |

---

### 系统状态 {#system-status}
**Endpoint**: `GET /api/v1/system/status`

**描述**: 获取系统状态和配置信息

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/system/status" -i

curl -X GET "http://localhost:8848/api/v1/system/status" \
  -H "X-API-Key: test-api-key-123" -i
```
**响应示例**:
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
**使用说明**:

| 场景 | 预期结果 |
| --- | --- |
| 正常请求 | 返回 200 和系统信息 |
| 无 API Key | 返回 401 Unauthorized |
| 无效的 API Key | 返回 401 Unauthorized |

---

### 系统指标 {#system-metrics}
**Endpoint**: `GET /api/v1/system/metrics`

**描述**: 获取 Prometheus 格式的指标（占位实现）

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/system/metrics" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
### 删除所有记忆 {#delete-all-memories}
**Endpoint**: `DELETE /api/v1/system/delete-all-memories`

**描述**: 删除所有记忆

**请求示例**:
```bash
# 删除所有记忆（系统级）
curl -X DELETE "http://localhost:8848/api/v1/system/delete-all-memories" \
  -H "X-API-Key: test-api-key-123"

# 删除指定 Agent 的所有记忆
curl -X DELETE "http://localhost:8848/api/v1/system/delete-all-memories?agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# 删除指定用户的所有记忆
curl -X DELETE "http://localhost:8848/api/v1/system/delete-all-memories?user_id=user-123" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
```json
{
  "success": true,
  "data": {},
  "message": "All memories reset successfully",
  "timestamp": "2025-12-24T08:24:29.170996Z"
}
```
**使用说明**:

| 场景 | 预期结果 |
| --- | --- |
| 正常删除 | 返回 200，所有记忆被删除 |
| 删除后查询 | 返回空列表 |

## 记忆管理端点 {#memory-management-endpoints}
### 创建记忆 {#create-memory}
**端点**: `POST /api/v1/memories`

**描述**: 创建一个新的记忆

**请求示例**:
```bash
curl -X POST "http://localhost:8848/api/v1/memories" \
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 最小参数 | 仅包含 content | 返回 200，创建成功 |
| 完整参数 | 包含所有字段 | 返回 200，所有字段保存成功 |
| 缺少 content | 无 content 字段 | 返回 422 验证错误 |
| 空 content | content 为空字符串 | 返回 422 验证错误 |
| 无效的 metadata | metadata 格式错误 | 返回 422 验证错误 |

---

### 批量创建记忆 {#batch-create-memories}
**Endpoint**: `POST /api/v1/memories/batch`

**描述**: 批量创建多个记忆

**请求示例**:
```bash
curl -X POST "http://localhost:8848/api/v1/memories/batch" \
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
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常批量创建 | 2-10 memories | 返回 200，全部创建成功 |
| 部分失败 | 包含无效数据 | 返回 200，failed_count > 0 |
| 空列表 | memories 为空 | 返回 422 验证错误 |
| 超出限制 | > 100 memories | 返回 422 验证错误 |
| 成功与失败混合 | 部分有效，部分无效 | 返回 200，显示成功和失败的计数 |

---

### 列出记忆 {#list-memories}
**Endpoint**: `GET /api/v1/memories`

**描述**: 获取支持分页、过滤和排序的记忆列表

**查询参数**:
- `user_id` (可选): 按用户 ID 过滤
- `agent_id` (可选): 按 Agent ID 过滤
- `scope` (可选): 按元数据范围过滤，例如个人记忆或群组记忆
- `limit` (可选，默认值: 100): 返回结果的最大数量 (1-1000)
- `offset` (可选，默认值: 0): 跳过的结果数量
- `sort_by` (可选): 排序字段。选项: `created_at`, `updated_at`, `id`。如果未指定，结果按原始顺序返回
- `order` (可选，默认值: `desc`): 排序顺序。选项: `desc` (降序), `asc` (升序)

**请求示例**:
```bash
# 基础查询
curl -X GET "http://localhost:8848/api/v1/memories?limit=10&offset=0" \
  -H "X-API-Key: test-api-key-123"

# 按用户过滤
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"

# 按 Agent 过滤
curl -X GET "http://localhost:8848/api/v1/memories?agent_id=agent-456&limit=50&offset=0" \
  -H "X-API-Key: test-api-key-123"

# 按 metadata scope 过滤
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&agent_id=agent-456&scope=personal&limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"

# 按 updated_at 排序（降序，最新优先）
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&limit=10&sort_by=updated_at&order=desc" \
  -H "X-API-Key: test-api-key-123"

# 按 created_at 排序（升序，最旧优先）
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&limit=10&sort_by=created_at&order=asc" \
  -H "X-API-Key: test-api-key-123"

# 组合：过滤、分页和排序
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&agent_id=agent-456&limit=20&offset=0&sort_by=updated_at&order=desc" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 默认分页 | 无参数 | 返回 limit=100, offset=0 |
| 自定义分页 | limit=20, offset=10 | 返回 20 条记录，跳过前 10 条 |
| 按用户过滤 | user_id=user-123 | 仅返回该用户的记忆 |
| 按 Agent 过滤 | agent_id=agent-456 | 仅返回该 Agent 的记忆 |
| 组合过滤 | user_id + agent_id | 返回同时匹配两者的记录 |
| 按 updated_at 降序排序 | sort_by=updated_at&order=desc | 返回按更新时间排序的记忆，最新的在前 |
| 按 created_at 升序排序 | sort_by=created_at&order=asc | 返回按创建时间排序的记忆，最早的在前 |
| 按 id 降序排序 | sort_by=id&order=desc | 返回按 ID 排序的记忆，ID 最大的在前 |
| 组合：过滤 + 排序 | user_id + sort_by=updated_at | 返回过滤并排序的结果 |
| 组合：过滤 + 排序 + 分页 | user_id + sort_by=updated_at + limit + offset | 返回过滤、排序并分页的结果 |
| 超出最大限制 | limit=2000 | 返回 422 验证错误 |
| 负偏移量 | offset=-1 | 返回 422 验证错误 |
| 结果为空 | 无匹配记录 | 返回空数组 |

---

### 获取记忆 {#get-memory}
**接口**: `GET /api/v1/memories/{memory_id}`

**描述**: 根据 ID 获取单条记忆

**请求示例**:
```bash
# 先列出所有记忆以查看可用 ID
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# 然后按指定 ID 查询
curl -X GET "http://localhost:8848/api/v1/memories/1?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常检索 | 已存在的 memory_id | 返回 200，包含完整的记忆信息 |
| 不存在的 ID | memory_id=99999 | 返回 404 Not Found |
| 无效的 ID | memory_id=abc | 返回 422 Validation Error |
| 访问控制 | 错误的 user_id | 返回 403 或 404 |
| 访问控制 | 错误的 agent_id | 返回 403 或 404 |


---

### 更新记忆 {#update-memory}
**Endpoint**: `PUT /api/v1/memories/{memory_id}`

**描述**: 更新一个已存在的记忆

**请求示例**:
```bash
# 先列出所有记忆以查看可用 ID
curl -X GET "http://localhost:8848/api/v1/memories?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"

# 更新内容
curl -X PUT "http://localhost:8848/api/v1/memories/658958031962243072" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "User likes latte coffee and goes to Starbucks every morning",
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'

# 更新 metadata
curl -X PUT "http://localhost:8848/api/v1/memories/658958031962243072" \
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

# 同时更新内容和 metadata
curl -X PUT "http://localhost:8848/api/v1/memories/658958031962243072" \
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
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 更新内容 | 仅 content | 返回 200，内容已更新 |
| 更新元数据 | 仅 metadata | 返回 200，元数据已更新 |
| 同时更新 | content + metadata | 返回 200，内容和元数据均已更新 |
| 无更新字段 | content 和 metadata 均为空 | 返回 400 错误 |
| 不存在的 ID | memory_id=99999 | 返回 404 未找到 |
| 访问控制 | 错误的 user_id | 返回 403 或 404 |

---

### 批量更新记忆 {#batch-update-memories}
**接口**: `PUT /api/v1/memories/batch`

**描述**: 批量更新多个记忆

**请求示例**:
```bash
curl -X PUT "http://localhost:8848/api/v1/memories/batch" \
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
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常批量更新 | 2-10 个更新 | 返回 200，全部更新成功 |
| 部分失败 | 包含不存在的 ID | 返回 200，failed_count > 0 |
| 空列表 | updates 为空 | 返回 422 验证错误 |
| 超出限制 | > 100 个更新 | 返回 422 验证错误 |

---

### 删除记忆 {#delete-memory}
**Endpoint**: `DELETE /api/v1/memories/{memory_id}`

**描述**: 删除单个记忆

**请求示例**:
```bash
curl -X DELETE "http://localhost:8848/api/v1/memories/658958021480677376?user_id=user-123&agent_id=agent-456" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常删除 | 已存在的 memory_id | 返回 200，删除成功 |
| 不存在的 ID | memory_id=99999 | 返回 404 Not Found |
| 删除后查询 | 再次查询相同 ID | 返回 404 Not Found |
| 访问控制 | 错误的 user_id | 返回 403 或 404 |

---

### 批量删除记忆 {#bulk-delete-memories}
**Endpoint**: `DELETE /api/v1/memories/batch`

**描述**: 批量删除多个记忆

**请求示例**:
```bash
curl -X DELETE "http://localhost:8848/api/v1/memories/batch" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_ids": [658958031962243072, 658968835172335616, 658968835277193216],
    "user_id": "user-123",
    "agent_id": "agent-456"
  }'
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常批量删除 | 5-10 个 ID | 返回 200，全部成功删除 |
| 部分失败 | 包含不存在的 ID | 返回 200，failed_count > 0 |
| 空列表 | memory_ids 为空 | 返回 422 验证错误 |
| 超出限制 | > 100 个 ID | 返回 422 验证错误 |


---

## 搜索端点 {#search-endpoints}
### 搜索记忆 (POST) {#search-memories-post}
**端点**: `POST /api/v1/memories/search`

**描述**: 使用语义搜索进行记忆搜索（支持复杂过滤）

**请求示例**:
```bash
# 先创建一些数据
curl -X POST "http://localhost:8848/api/v1/memories"   -H "X-API-Key: test-api-key-123"   -H "Content-Type: application/json"   -d '{
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

# 搜索
curl -X POST "http://localhost:8848/api/v1/memories/search" \
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
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 基础搜索 | 仅 query | 返回相关记忆，按相关性排序 |
| 使用用户过滤 | query + user_id | 仅返回该用户的记忆 |
| 使用 Agent 过滤 | query + agent_id | 仅返回该 Agent 的记忆 |
| 使用复杂过滤条件 | query + filters | 返回符合过滤条件的记忆 |
| 限制结果数量 | limit=5 | 最多返回5条结果 |
| 空查询 | query为空 | 返回422验证错误 |
| 无结果 | 无匹配记忆 | 返回空数组 |
| 超出最大限制 | limit=200 | 返回422验证错误 |

---

### 搜索记忆 (GET) {#search-memories-get}
**接口**: `POST /api/v1/memories/search`

**描述**: 使用查询参数进行搜索

**请求示例**:
```bash
curl -X POST "http://localhost:8848/api/v1/memories/search" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What beverages does the user like",
    "user_id": "user-123",
    "agent_id": "agent-456",
    "limit": 10
  }'
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 基本搜索 | query 参数 | 返回相关记忆 |
| 带过滤条件 | query + user_id + agent_id | 返回匹配的记忆 |
| 缺少 query | 无 query 参数 | 返回 422 验证错误 |
| URL 编码 | 非 ASCII 的 query | 正确处理 URL 编码 |

---

## 用户档案端点 {#user-profile-endpoints}
### 添加消息并提取用户档案 {#add-messages-and-extract-user-profile}
**端点**: `POST /api/v1/users/{user_id}/profile`

**描述**: 添加对话消息并提取用户档案信息

**请求示例**:
```bash
# 添加消息并提取档案（默认仅从 user 消息提取）
curl -X POST "http://localhost:8848/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Hi, I am a senior software engineer from Beijing. I focus on AI and machine learning."},
      {"role": "assistant", "content": "Nice to meet you! That sounds interesting."}
    ],
    "agent_id": "agent-456",
    "run_id": "run-789",
    "profile_type": "content",
    "include_roles": ["user"],
    "exclude_roles": ["assistant"],
    "infer": true
  }'

# 提取结构化 topic
curl -X POST "http://localhost:8848/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "I am Alice, 28 years old, working as a data scientist in Shanghai."}
    ],
    "profile_type": "topics",
    "custom_topics": "{\"basic_info\": {\"name\": \"User name\", \"age\": \"User age\", \"location\": \"User location\"}, \"professional\": {\"occupation\": \"User job\"}}",
    "strict_mode": false
  }'

# 包含所有消息（禁用角色过滤）
curl -X POST "http://localhost:8848/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "I am Bob, a doctor."},
      {"role": "assistant", "content": "Nice to meet you!"}
    ],
    "include_roles": null,
    "exclude_roles": null
  }'
```
**响应示例**:
```json
{
    "success": true,
    "data": {
        "results": [
            {
                "id": 658954684471443456,
                "memory": "User is a senior software engineer from Beijing",
                "event": "ADD",
                "user_id": "user-123",
                "agent_id": "agent-456",
                "run_id": "run-789"
            }
        ],
        "profile_extracted": true,
        "profile_content": "Name: Unknown. Location: Beijing. Profession: Senior software engineer. Interests: AI, machine learning."
    },
    "message": "Messages added and profile extracted successfully",
    "timestamp": "2025-12-24T10:31:13.195518Z"
}
```
**请求参数**:

| 参数 | 类型 | 是否必需 | 默认值 | 描述 |
| --- | --- | --- | --- | --- |
| messages | any | 是 | - | 对话消息（str、dict 或 list[dict]） |
| agent_id | string | 否 | null | Agent 标识符 |
| run_id | string | 否 | null | 运行/会话标识符 |
| metadata | object | 否 | null | 附加元数据 |
| filters | object | 否 | null | 用于高级过滤的元数据过滤器 |
| scope | string | 否 | null | 记忆范围 |
| memory_type | string | 否 | null | 记忆类型分类 |
| prompt | string | 否 | null | 用于智能处理的自定义 prompt |
| infer | boolean | 否 | true | 启用智能记忆处理 |
| profile_type | string | 否 | "content" | 提取配置类型："content" 或 "topics" |
| custom_topics | string | 否 | null | 自定义主题的 JSON 字符串（仅适用于 profile_type="topics"） |
| strict_mode | boolean | 否 | false | 仅输出提供列表中的主题 |
| include_roles | list | 否 | ["user"] | 过滤消息时包含的角色。设置为 null 或 [] 以禁用 |
| exclude_roles | list | 否 | ["assistant"] | 过滤消息时排除的角色。设置为 null 或 [] 以禁用 |

**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 基本提取 | 仅 messages | 返回 200，从用户消息中提取配置 |
| 包含 Agent/运行 | messages + agent_id + run_id | 返回 200，与 Agent 和运行相关联 |
| 提取主题 | profile_type="topics" | 返回 200，提取结构化主题 |
| 自定义角色过滤 | include_roles=["user", "system"] | 返回 200，从指定角色中提取 |
| 无角色过滤 | include_roles=null, exclude_roles=null | 返回 200，从所有消息中提取 |
| 缺少消息 | 无 messages 字段 | 返回 422 验证错误 |

---

### 更新用户记忆 {#update-user-memory}
**端点**: `PUT /api/v1/users/{user_id}/memories/{memory_id}`

**描述**: 更新特定用户的现有记忆

**请求示例**:
```bash
curl -X PUT "http://localhost:8848/api/v1/users/user-123/memories/658954684471443456" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "User is a senior AI engineer from Beijing, specializing in NLP",
    "agent_id": "agent-456",
    "metadata": {
      "importance": "high",
      "updated_by": "admin"
    }
  }'
```
**响应示例**:
```json
{
    "success": true,
    "data": {
        "memory_id": 658954684471443456,
        "content": "User is a senior AI engineer from Beijing, specializing in NLP",
        "user_id": "user-123",
        "agent_id": "agent-456",
        "metadata": {
            "importance": "high",
            "updated_by": "admin"
        },
        "created_at": "2025-12-24T10:31:13.169725Z",
        "updated_at": "2025-12-24T11:45:30.123456Z"
    },
    "message": "Memory updated successfully",
    "timestamp": "2025-12-24T11:45:30.150000Z"
}
```
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 更新内容 | content 字段 | 返回 200，内容已更新 |
| 更新内容和元数据 | content + metadata | 返回 200，二者均已更新 |
| 不存在的记忆 | 无效的 memory_id | 返回 404 Not Found |
| 访问控制 | 错误的 user_id | 返回 403 或 404 |

---

### 获取用户资料 {#get-user-profile}
**接口**: `GET /api/v1/users/{user_id}/profile`

**描述**: 获取特定用户的资料

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常检索 | 已存在的 user_id | 返回 200 和用户资料 |
| 用户不存在 | user_id=unknown | 返回 404 或空的用户资料 |
| 用户无资料 | 新用户 | 返回空的 profile_content |

---

### 删除用户资料 {#delete-user-profile}
**接口**: `DELETE /api/v1/users/{user_id}/profile`

**描述**: 删除特定用户的用户资料

**请求示例**:
```bash
curl -X DELETE "http://localhost:8848/api/v1/users/user-123/profile" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常删除 | 存在的 user_id 和 profile | 返回 200，profile 删除成功 |
| 用户不存在 | user_id=unknown | 返回 404 Not Found |
| 用户无 profile | 用户没有 profile | 返回 404 Not Found |
| 删除后查询 | 再次查询相同用户 | 返回 404 Not Found |

---

### 获取用户记忆 {#get-user-memories}
**Endpoint**: `GET /api/v1/users/{user_id}/memories`

**描述**: 获取特定用户的所有记忆

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/users/user-123/memories?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常检索 | 已存在的 user_id | 返回该用户的所有记忆 |
| 分页查询 | limit=10, offset=10 | 返回分页结果 |
| 无记忆的用户 | 新用户 | 返回空数组 |
| 不存在的用户 | user_id=unknown | 返回空数组或 404 |

---

### 删除用户记忆 {#delete-user-memories}
**接口**: `DELETE /api/v1/users/{user_id}/memories`

**描述**: 删除特定用户的所有记忆（用户资料删除）

**请求示例**:
```bash
curl -X DELETE "http://localhost:8848/api/v1/users/user-123/memories" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常删除 | 已存在的 user_id | 返回 200，所有记忆被删除 |
| 无记忆的用户 | 新用户 | 返回 deleted_count=0 |
| 删除后的查询 | 再次查询同一用户 | 返回空数组 |

---

## Agent 管理端点 {#agent-management-endpoints}

### 创建 Agent 记忆 {#create-agent-memory}
**端点**: `POST /api/v1/agents/{agent_id}/memories`

**描述**: 为特定 Agent 创建记忆

**请求示例**:
```bash
curl -X POST "http://localhost:8848/api/v1/agents/agent-456/memories"   -H "X-API-Key: test-api-key-123"   -H "Content-Type: application/json"   -d '{
    "content": "Agent learned new conversation techniques",
    "user_id": "user-123",
    "run_id": "run-789"
  }'
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常创建 | content 参数 | 返回 200，创建成功 |
| 包含用户 ID | content + user_id | 返回 200，与用户关联 |
| 包含运行 ID | content + run_id | 返回 200，与运行关联 |
| 缺少内容 | 无 content | 返回 422 验证错误 |

---

### 获取 Agent 的记忆 {#get-agent-memories}
**Endpoint**: `GET /api/v1/agents/{agent_id}/memories`

**描述**: 获取特定 Agent 的所有记忆

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/agents/agent-456/memories?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常检索 | 已存在的 agent_id | 返回该 Agent 的所有记忆 |
| 分页查询 | limit=10, offset=10 | 返回分页结果 |
| 无记忆的 Agent | 新的 Agent | 返回空数组 |

---

### 分享 Agent 记忆 {#share-agent-memories}
**接口**: `POST /api/v1/agents/{agent_id}/memories/share`

**描述**: 将 Agent 的记忆分享给另一个 Agent

**请求示例**:
```bash
# 共享所有记忆
curl -X POST "http://localhost:8848/api/v1/agents/agent-456/memories/share" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "target_agent_id": "agent-789"
  }'

# 共享指定记忆
curl -X POST "http://localhost:8848/api/v1/agents/agent-456/memories/share" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "target_agent_id": "agent-789",
    "memory_ids": [1, 2, 3]
  }'
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 分享所有记忆 | 仅 target_agent_id | 返回 200，所有记忆已分享 |
| 分享特定记忆 | target_agent_id + memory_ids | 返回 200，指定记忆已分享 |
| 不存在的记忆 ID | 无效的 memory_ids | 返回 200，shared_count 小于请求数量 |
| 不存在的目标 Agent | target_agent_id=unknown | 返回 200，创建新的 Agent 记录 |

---

### 获取共享记忆 {#get-shared-memories}
**接口**: `GET /api/v1/agents/{agent_id}/memories/share`

**描述**: 获取某个 Agent 接收到的共享记忆

**请求示例**:
```bash
curl -X GET "http://localhost:8848/api/v1/agents/agent-789/memories/share?limit=20&offset=0" \
  -H "X-API-Key: test-api-key-123"
```
**响应示例**:
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
**使用说明**:

| 场景 | 请求参数 | 预期结果 |
| --- | --- | --- |
| 正常检索 | 已存在的 agent_id | 返回共享记忆列表 |
| 无共享记忆 | 新的 Agent | 返回空数组 |
| 分页查询 | limit=10, offset=10 | 返回分页结果 |

---

## 错误场景 {#error-scenarios}
### 认证错误 {#authentication-errors}
**错误案例**:

| 场景 | 请求 | 预期结果 |
| --- | --- | --- |
| 无 API Key | 缺少 X-API-Key 头部 | 返回 401 Unauthorized |
| 无效的 API Key | X-API-Key: invalid-key | 返回 401 Unauthorized |
| 空的 API Key | X-API-Key: (空) | 返回 401 Unauthorized |

**示例**:
```bash
# 无 API Key
curl -X GET "http://localhost:8848/api/v1/memories"

# 响应
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "API key is required"
  }
}
```
---

### 速率限制 {#rate-limiting}
**错误情况**：

| 场景 | 请求 | 预期结果 |
| --- | --- | --- |
| 正常请求 | 单次请求 | 返回 200 |
| 超出速率限制 | 短时间内大量请求 | 返回 429 Too Many Requests |

**示例**：
```bash
# 快速发送 200 个请求
for i in {1..200}; do
  curl -X GET "http://localhost:8848/api/v1/memories" \
    -H "X-API-Key: test-api-key-123" &
done

# 响应（超过限流时）
{
  "success": false,
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded: 100 per minute"
  }
}
```
---

### 验证错误 {#validation-errors}
**错误情况**:

| 场景 | 请求 | 预期结果 |
| --- | --- | --- |
| 缺少必填字段 | 缺少内容 | 返回 422 验证错误 |
| 类型错误 | memory_id="abc" | 返回 422 验证错误 |
| 范围错误 | limit=2000 | 返回 422 验证错误 |
| 格式错误 | 无效的 JSON 格式 | 返回 422 验证错误 |


**示例**:
```bash
# 缺少必填字段
curl -X POST "http://localhost:8848/api/v1/memories" \
  -H "X-API-Key: test-api-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-123"
  }'

# 响应
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Field 'content' is required"
  }
}
```
---

### 资源未找到 {#resource-not-found}
**错误情况**：

| 场景 | 请求 | 预期结果 |
| --- | --- | --- |
| 不存在的记忆 | GET /memories/99999 | 返回 404 Not Found |
| 不存在的用户 | GET /users/unknown/profile | 返回 404 或空数据 |
| 不存在的 Agent | GET /agents/unknown/memories | 返回空数组 |


---

### 服务器错误 {#server-errors}
**错误情况**：

| 场景 | 请求 | 预期结果 |
| --- | --- | --- |
| 数据库连接失败 | 任意请求 | 返回 500 Internal Server Error |
| 服务不可用 | 任意请求 | 返回 503 Service Unavailable |


---

## 性能测试 {#performance-testing}
### 响应时间测试 {#response-time-testing}
使用工具测量端点的响应时间：
```bash
# 使用 curl 测量响应时间
time curl -X GET "http://localhost:8848/api/v1/memories" \
  -H "X-API-Key: test-api-key-123"

# 使用 httpie
http --timeout=5 GET "http://localhost:8848/api/v1/memories" \
  X-API-Key:test-api-key-123
```
---

### 并发测试 {#concurrent-testing}
使用工具进行并发负载测试：
```bash
# 使用 Apache Bench
ab -n 1000 -c 10 -H "X-API-Key: test-api-key-123" \
  http://localhost:8848/api/v1/memories

# 使用 wrk
wrk -t4 -c100 -d30s -H "X-API-Key: test-api-key-123" \
  http://localhost:8848/api/v1/memories
```

---
