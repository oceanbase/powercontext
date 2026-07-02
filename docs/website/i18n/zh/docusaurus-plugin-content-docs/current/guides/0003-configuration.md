# 配置参考 {#configuration-reference}

PowerMem 所有配置选项的完整参考指南。本文件详细解释了 `env.example` 中的每个配置参数。

## 配置方法 {#configuration-methods}

PowerMem 支持两种配置方法：

1. **环境变量（`.env` 文件）** - 推荐用于大多数使用场景
2. **JSON/字典配置** - 适用于编程方式的配置

### 方法 1：环境变量 {#method-1-environment-variables}

在项目根目录创建一个 `.env` 文件，并通过环境变量进行配置。请参阅以下各部分中的示例。
```python
from powermem import Memory, auto_config

# 加载配置（自动从 .env 加载或使用默认值）
config = auto_config()

# 创建 Memory 实例
memory = Memory(config=config)
```
### 方法 2：JSON/字典配置 {#method-2-jsondictionary-configuration}

以 Python 字典（类似 JSON 的格式）传递配置。这在以下情况下非常有用：
- 从 JSON 文件加载配置
- 以编程方式生成配置
- 在应用程序代码中嵌入配置
```python
from powermem import Memory

config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'text-embedding-v4'
        }
    }
}

memory = Memory(config=config)
```
### 从 JSON 文件加载 {#loading-from-json-file}

您还可以从 JSON 文件加载配置：
```python
import json
from json import dump, dumps
from powermem import Memory

# 从 JSON 文件加载
with open('config.json', 'r') as f:
    config = json.load(f)

memory = Memory(config=config)
```
## 目录 {#table-of-contents}

1. [数据库配置](#1-database-configuration-required)
2. [LLM 配置](#2-llm-configuration-required)
3. [Embedding 配置](#3-embedding-configuration-required)
4. [Agent 配置](#4-agent-configuration-optional)
5. [智能记忆配置](#5-intelligent-memory-configuration-optional)
6. [性能配置](#6-performance-configuration-optional)
7. [安全配置](#7-security-configuration-optional)
8. [遥测配置](#8-telemetry-configuration-optional)
9. [审计配置](#9-audit-configuration-optional)
10. [日志配置](#10-logging-configuration-optional)

---

## 1. 数据库配置 (必需) {#1-database-configuration-required}

PowerMem 需要一个数据库提供程序来存储记忆和向量。请选择以下支持的提供程序之一：SQLite（开发环境）、OceanBase（生产环境）或 PostgreSQL。

### 通用数据库设置 {#common-database-settings}

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `DATABASE_PROVIDER` | string | 是 | `sqlite` | 要使用的数据库提供程序。选项：`sqlite`、`oceanbase`、`postgres` |

### SQLite 配置 {#sqlite-configuration}

SQLite 是默认的数据库提供程序，推荐用于开发和单用户应用程序。

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `SQLITE_PATH` | string | 是* | `./data/powermem_dev.db` | SQLite 数据库文件的路径。当 `DATABASE_PROVIDER=sqlite` 时必需 |
| `SQLITE_ENABLE_WAL` | boolean | 否 | `true` | 启用预写日志（WAL）模式以提高并发性 |
| `SQLITE_TIMEOUT` | integer | 否 | `30` | 连接超时时间（秒） |

**环境变量示例：**
```env
DATABASE_PROVIDER=sqlite
SQLITE_PATH=./data/powermem_dev.db
SQLITE_ENABLE_WAL=true
SQLITE_TIMEOUT=30
```
**JSON 配置示例：**
```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db",
      "enable_wal": true,
      "timeout": 30
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db',
            'enable_wal': True,
            'timeout': 30
        }
    }
}
```
**SQLite 的要求和限制：**

- 需要 SQLite ≥ 3.9.0（支持 FTS5 和 JSON1 扩展）。运行
  `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` 进行验证。如果系统自带的 SQLite 版本过旧，可以安装 `pysqlite3-binary`（包含 SQLite 3.45+）。
- 强烈推荐为编码 Agent 使用 WAL 模式（`SQLITE_ENABLE_WAL=true`）：`UserPromptSubmit`（回忆）和 `SessionEnd`（保存）钩子可能会并发运行，而 WAL 模式允许读写操作在没有独占锁的情况下进行。
- 以下功能在 SQLite 中不可用并会被静默禁用：子存储、源存储、技能存储、图存储、稀疏向量、原生混合搜索和数据迁移。所有核心记忆操作（添加、搜索、更新、删除、艾宾浩斯遗忘曲线衰减）均完全支持。
- 向量搜索使用纯 Python 的余弦相似度（O(n) 线性扫描）。对于单个编码 Agent 和典型的记忆量，这种方式足够快；对于大规模部署，建议使用 OceanBase。

### OceanBase 配置 {#oceanbase-configuration}

OceanBase 推荐用于生产部署和具有高规模需求的企业应用。

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|-------|------|
| `OCEANBASE_HOST` | string | 是* | `localhost` | OceanBase 服务器主机名或 IP 地址。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_PORT` | integer | 是* | `2881` | OceanBase 服务器端口。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_USER` | string | 是* | `root` | 数据库用户名。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_PASSWORD` | string | 是* | - | 数据库密码。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_DATABASE` | string | 是* | `powermem` | 数据库名称。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_COLLECTION` | string | 否 | `memories` | 用于存储记忆的集合/表名称 |
| `OCEANBASE_INDEX_TYPE` | string | 否 | `IVF_FLAT` | 向量索引类型。选项：`IVF_FLAT`、`HNSW` 等 |
| `OCEANBASE_VECTOR_METRIC_TYPE` | string | 否 | `cosine` | 向量相似度度量方式。选项：`cosine`、`euclidean`、`dot_product` |
| `OCEANBASE_TEXT_FIELD` | string | 否 | `document` | 用于存储文本内容的字段名称 |
| `OCEANBASE_VECTOR_FIELD` | string | 否 | `embedding` | 用于存储向量嵌入的字段名称 |
| `OCEANBASE_EMBEDDING_MODEL_DIMS` | integer | 是* | `1536` | 向量维度。必须与嵌入模型的维度匹配。当 `DATABASE_PROVIDER=oceanbase` 时必需 |
| `OCEANBASE_PRIMARY_FIELD` | string | 否 | `id` | 主键字段名称 |
| `OCEANBASE_METADATA_FIELD` | string | 否 | `metadata` | 用于存储元数据的字段名称 |
| `OCEANBASE_VIDX_NAME` | string | 否 | `memories_vidx` | 向量索引名称 |

**环境变量示例：**
```env
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=localhost
OCEANBASE_PORT=2881
OCEANBASE_USER=root
OCEANBASE_PASSWORD=your_password
OCEANBASE_DATABASE=powermem
OCEANBASE_COLLECTION=memories
OCEANBASE_INDEX_TYPE=IVF_FLAT
OCEANBASE_VECTOR_METRIC_TYPE=cosine
OCEANBASE_EMBEDDING_MODEL_DIMS=1536
```
**JSON 配置示例：**
```json
{
  "vector_store": {
    "provider": "oceanbase",
    "config": {
      "collection_name": "memories",
      "connection_args": {
        "host": "localhost",
        "port": 2881,
        "user": "root",
        "password": "your_password",
        "db_name": "powermem"
      },
      "vidx_metric_type": "cosine",
      "index_type": "IVF_FLAT",
      "embedding_model_dims": 1536,
      "primary_field": "id",
      "vector_field": "embedding",
      "text_field": "document",
      "metadata_field": "metadata",
      "vidx_name": "memories_vidx"
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': 'localhost',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            },
            'vidx_metric_type': 'cosine',
            'index_type': 'IVF_FLAT',
            'embedding_model_dims': 1536
        }
    }
}
```
### PostgreSQL 配置 {#postgresql-configuration}

支持使用带有 pgvector 扩展的 PostgreSQL 进行向量存储。

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `POSTGRES_HOST` | string | 是* | `localhost` | PostgreSQL 服务器的主机名或 IP 地址。当 `DATABASE_PROVIDER=postgres` 时必需 |
| `POSTGRES_PORT` | integer | 是* | `5432` | PostgreSQL 服务器端口。当 `DATABASE_PROVIDER=postgres` 时必需 |
| `POSTGRES_USER` | string | 是* | `postgres` | 数据库用户名。当 `DATABASE_PROVIDER=postgres` 时必需 |
| `POSTGRES_PASSWORD` | string | 是* | - | 数据库密码。当 `DATABASE_PROVIDER=postgres` 时必需 |
| `POSTGRES_DATABASE` | string | 是* | `powermem` | 数据库名称。当 `DATABASE_PROVIDER=postgres` 时必需 |
| `DATABASE_SSLMODE` | string | 否 | `prefer` | SSL 连接模式。选项：`disable`、`allow`、`prefer`、`require`、`verify-ca`、`verify-full` |
| `DATABASE_POOL_SIZE` | integer | 否 | `10` | 连接池大小 |
| `DATABASE_MAX_OVERFLOW` | integer | 否 | `20` | 连接池中最大溢出连接数 |

**环境变量示例：**
```env
DATABASE_PROVIDER=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_DATABASE=powermem
POSTGRES_COLLECTION=memories
DATABASE_SSLMODE=prefer
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
```
**JSON 配置示例：**
```json
{
  "vector_store": {
    "provider": "postgres",
    "config": {
      "collection_name": "memories",
      "dbname": "powermem",
      "host": "localhost",
      "port": 5432,
      "user": "postgres",
      "password": "your_password",
      "embedding_model_dims": 1536,
      "diskann": true,
      "hnsw": true
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'vector_store': {
        'provider': 'postgres',
        'config': {
            'collection_name': 'memories',
            'dbname': 'powermem',
            'host': 'localhost',
            'port': 5432,
            'user': 'postgres',
            'password': 'your_password',
            'embedding_model_dims': 1536
        }
    }
}
```
---

## 2. LLM 配置（必需） {#2-llm-configuration-required}

PowerMem 需要一个 LLM 提供商来生成和检索记忆。可选择 Qwen、OpenAI 或 Mock（用于测试）。

### 通用 LLM 设置 {#common-llm-settings}

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `LLM_PROVIDER` | string | 是 | `qwen` | 要使用的 LLM 提供商。选项：`qwen`、`openai`、`mock` |

### Qwen 配置（默认） {#qwen-configuration-default}

Qwen 是默认的 LLM 提供商，由阿里云 DashScope 提供支持。

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `LLM_API_KEY` | string | 是* | - | DashScope API 密钥。当 `LLM_PROVIDER=qwen` 时必需 |
| `LLM_MODEL` | string | 否 | `qwen-plus` | Qwen 模型名称。选项：`qwen-plus`、`qwen-max`、`qwen-turbo`、`qwen-long` 等 |
| `QWEN_LLM_BASE_URL` | string | 否 | `https://dashscope.aliyuncs.com/api/v1` | DashScope 的 API 基础 URL |
| `LLM_TEMPERATURE` | float | 否 | `0.7` | 采样温度（0.0-2.0）。较高的值使输出更随机 |
| `LLM_MAX_TOKENS` | integer | 否 | `1000` | 要生成的最大 token 数量 |
| `LLM_TOP_P` | float | 否 | `0.8` | 核采样参数（0.0-1.0）。控制输出的多样性 |
| `LLM_TOP_K` | integer | 否 | `50` | Top-K 采样参数。限制采样到前 K 个 token |
| `LLM_ENABLE_SEARCH` | boolean | 否 | `false` | 启用网页搜索功能（如果模型支持） |

**环境变量示例：**
```env
LLM_PROVIDER=qwen
LLM_API_KEY=your_api_key_here
LLM_MODEL=qwen-plus
QWEN_LLM_BASE_URL=https://dashscope.aliyuncs.com/api/v1
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1000
LLM_TOP_P=0.8
LLM_TOP_K=50
LLM_ENABLE_SEARCH=false
```
**JSON 配置示例:**
```json
{
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus",
      "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1",
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 0.8,
      "top_k": 50,
      "enable_search": false
    }
  }
}
```
**Python 字典示例：**
```python
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'dashscope_base_url': 'https://dashscope.aliyuncs.com/api/v1',
            'temperature': 0.7,
            'max_tokens': 1000,
            'top_p': 0.8,
            'top_k': 50,
            'enable_search': False
        }
    }
}
```
### OpenAI 配置 {#openai-configuration}

支持 OpenAI GPT 模型。

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|-------|------|
| `LLM_API_KEY` | string | 是* | - | OpenAI API 密钥。当 `LLM_PROVIDER=openai` 时必需 |
| `LLM_MODEL` | string | 否 | `gpt-4` | OpenAI 模型名称。可选值：`gpt-4`、`gpt-4-turbo`、`gpt-3.5-turbo` 等 |
| `OPENAI_LLM_BASE_URL` | string | 否 | `https://api.openai.com/v1` | OpenAI 的 API 基础 URL |
| `OPENAI_LLM_DEFAULT_HEADERS` | JSON/string | 否 | - | 用于 OpenAI 兼容网关的额外默认 HTTP 头。建议使用 JSON 对象以支持多个头信息。 |
| `LLM_TEMPERATURE` | float | 否 | `0.7` | 采样温度（0.0-2.0） |
| `LLM_MAX_TOKENS` | integer | 否 | `1000` | 生成的最大 token 数量 |
| `LLM_TOP_P` | float | 否 | `1.0` | 核采样参数（0.0-1.0） |

**环境变量示例：**
```env
LLM_PROVIDER=openai
LLM_API_KEY=your-openai-api-key
LLM_MODEL=gpt-4
OPENAI_LLM_BASE_URL=https://api.openai.com/v1
OPENAI_LLM_DEFAULT_HEADERS={"X-Custom-Header":"your-value"}
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1000
LLM_TOP_P=1.0
```
**JSON 配置示例:**
```json
{
  "llm": {
    "provider": "openai",
    "config": {
      "api_key": "your-openai-api-key",
      "model": "gpt-4",
      "openai_base_url": "https://api.openai.com/v1",
      "default_headers": {"X-Custom-Header": "your-value"},
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 1.0
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'llm': {
        'provider': 'openai',
        'config': {
            'api_key': 'your-openai-api-key',
            'model': 'gpt-4',
            'openai_base_url': 'https://api.openai.com/v1',
            'temperature': 0.7,
            'max_tokens': 1000,
            'top_p': 1.0
        }
    }
}
```
---

## 3. Embedding 配置（必需） {#3-embedding-configuration-required}

PowerMem 需要一个 Embedding 提供方将文本转换为向量 Embedding，以进行相似性搜索。

### 通用 Embedding 设置 {#common-embedding-settings}

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|-------|------|
| `EMBEDDING_PROVIDER` | string | 是 | `qwen` | 使用的 Embedding 提供方。选项：`qwen`、`openai`、`mock` |

### Qwen Embedding 配置（默认） {#qwen-embedding-configuration-default}

Qwen Embedding 由阿里云 DashScope 提供。

| 配置项 | 类型 | 必需 | 默认值 | 描述 |
|--------|------|------|-------|------|
| `EMBEDDING_API_KEY` | string | 是* | - | DashScope API 密钥。当 `EMBEDDING_PROVIDER=qwen` 时必需 |
| `EMBEDDING_MODEL` | string | 否 | `text-embedding-v4` | Qwen Embedding 模型名称 |
| `EMBEDDING_DIMS` | integer | 是* | `1536` | 向量维度。如果使用 OceanBase，必须与 `DATABASE_EMBEDDING_MODEL_DIMS` 匹配。当 `EMBEDDING_PROVIDER=qwen` 时必需 |
| `QWEN_EMBEDDING_BASE_URL` | string | 否 | `https://dashscope.aliyuncs.com/api/v1` | DashScope 的 API 基础 URL |

**环境变量示例：**
```env
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_api_key_here
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536
QWEN_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/api/v1
```
**JSON 配置示例：**
```json
{
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    }
}
```
### OpenAI Embedding 配置 {#openai-embedding-configuration}

OpenAI 提供文本嵌入模型。

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `EMBEDDING_API_KEY` | string | 是* | - | OpenAI API 密钥。当 `EMBEDDING_PROVIDER=openai` 时必需 |
| `EMBEDDING_MODEL` | string | 否 | `text-embedding-ada-002` | OpenAI 嵌入模型名称。选项包括：`text-embedding-ada-002`、`text-embedding-3-small`、`text-embedding-3-large` |
| `EMBEDDING_DIMS` | integer | 是* | `1536` | 向量维度。根据模型不同而变化（ada-002: 1536, 3-small: 1536, 3-large: 3072）。当 `EMBEDDING_PROVIDER=openai` 时必需 |
| `OPEN_EMBEDDING_BASE_URL` | string | 否 | `https://api.openai.com/v1` | OpenAI 的 API 基础 URL |
| `OPENAI_EMBEDDING_DEFAULT_HEADERS` | JSON/string | 否 | - | 用于 OpenAI 兼容嵌入网关的额外默认 HTTP 头。建议使用 JSON 对象以支持多个头信息。 |

**环境变量示例：**
```env
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=your-openai-api-key
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_DIMS=1536
OPEN_EMBEDDING_BASE_URL=https://api.openai.com/v1
OPENAI_EMBEDDING_DEFAULT_HEADERS={"X-Custom-Header":"your-value"}
```
**JSON 配置示例:**
```json
{
  "embedder": {
    "provider": "openai",
    "config": {
      "api_key": "your-openai-api-key",
      "model": "text-embedding-ada-002",
      "embedding_dims": 1536
    }
  }
}
```
**Python 字典示例:**
```python
config = {
    'embedder': {
        'provider': 'openai',
        'config': {
            'api_key': 'your-openai-api-key',
            'model': 'text-embedding-ada-002',
            'embedding_dims': 1536
        }
    }
}
```
---

## 4. Agent 配置（可选） {#4-agent-configuration-optional}

Agent 配置控制 PowerMem 如何管理 AI Agent 的记忆。

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `AGENT_ENABLED` | boolean | 否 | `true` | 启用 Agent 记忆管理 |
| `AGENT_DEFAULT_SCOPE` | string | 否 | `AGENT` | Agent 记忆的默认范围。选项：`AGENT`，`USER`，`GLOBAL` |
| `AGENT_DEFAULT_PRIVACY_LEVEL` | string | 否 | `PRIVATE` | 默认隐私级别。选项：`PRIVATE`，`PUBLIC`，`SHARED` |
| `AGENT_DEFAULT_COLLABORATION_LEVEL` | string | 否 | `READ_ONLY` | 默认协作级别。选项：`READ_ONLY`，`READ_WRITE`，`FULL` |
| `AGENT_DEFAULT_ACCESS_PERMISSION` | string | 否 | `OWNER_ONLY` | 默认访问权限。选项：`OWNER_ONLY`，`AUTHORIZED`，`PUBLIC` |
| `AGENT_MEMORY_MODE` | string | 否 | `auto` | Agent 记忆模式。选项：`auto`，`multi_agent`，`multi_user`，`hybrid` |

**环境变量示例：**
```env
AGENT_ENABLED=true
AGENT_DEFAULT_SCOPE=AGENT
AGENT_DEFAULT_PRIVACY_LEVEL=PRIVATE
AGENT_DEFAULT_COLLABORATION_LEVEL=READ_ONLY
AGENT_DEFAULT_ACCESS_PERMISSION=OWNER_ONLY
AGENT_MEMORY_MODE=auto
```
**JSON 配置示例：**
```json
{
  "agent_memory": {
    "enabled": true,
    "mode": "auto",
    "default_scope": "AGENT",
    "default_privacy_level": "PRIVATE",
    "default_collaboration_level": "READ_ONLY",
    "default_access_permission": "OWNER_ONLY"
  }
}
```
**Python 字典示例:**
```python
config = {
    'agent_memory': {
        'enabled': True,
        'mode': 'auto',
        'default_scope': 'AGENT',
        'default_privacy_level': 'PRIVATE',
        'default_collaboration_level': 'READ_ONLY',
        'default_access_permission': 'OWNER_ONLY'
    }
}
```
---

## 5. 智能记忆配置（可选） {#5-intelligent-memory-configuration-optional}

智能记忆使用艾宾浩斯遗忘曲线来管理记忆的保持和衰减。

### 艾宾浩斯遗忘曲线设置 {#ebbinghaus-forgetting-curve-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `INTELLIGENT_MEMORY_ENABLED` | boolean | 否 | `true` | 启用智能记忆管理 |
| `INTELLIGENT_MEMORY_INITIAL_RETENTION` | float | 否 | `1.0` | 初始保持分数（0.0-1.0）。起始记忆强度 |
| `INTELLIGENT_MEMORY_DECAY_RATE` | float | 否 | `1.5` | 记忆衰减强度（S 在 `R=e^(-t/24S)` 中）。值越大，衰减越慢 |
| `INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR` | float | 否 | `0.3` | 增强因子（0.0-1.0）。访问时记忆增强的程度 |
| `INTELLIGENT_MEMORY_WORKING_THRESHOLD` | float | 否 | `0.3` | 工作记忆阈值（0.0-1.0）。低于此值的记忆属于工作记忆 |
| `INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD` | float | 否 | `0.6` | 短期记忆阈值（0.0-1.0）。介于工作记忆和此值之间的记忆属于短期记忆 |
| `INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD` | float | 否 | `0.8` | 长期记忆阈值（0.0-1.0）。高于此值的记忆属于长期记忆 |

### 记忆衰减计算设置 {#memory-decay-calculation-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `MEMORY_DECAY_ENABLED` | boolean | 否 | `true` | 启用记忆衰减计算 |
| `MEMORY_DECAY_ALGORITHM` | string | 否 | `ebbinghaus` | 使用的衰减算法。选项：`ebbinghaus` |
| `MEMORY_DECAY_BASE_RETENTION` | float | 否 | `1.0` | 基础保持分数（0.0-1.0） |
| `MEMORY_DECAY_FORGETTING_RATE` | float | 否 | `0.1` | 遗忘率（0.0-1.0） |
| `MEMORY_DECAY_REINFORCEMENT_FACTOR` | float | 否 | `0.3` | 衰减计算的增强因子（0.0-1.0） |

**环境变量示例：**
```env
INTELLIGENT_MEMORY_ENABLED=true
INTELLIGENT_MEMORY_INITIAL_RETENTION=1.0
INTELLIGENT_MEMORY_DECAY_RATE=0.1
INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR=0.3
INTELLIGENT_MEMORY_WORKING_THRESHOLD=0.3
INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD=0.6
INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD=0.8
MEMORY_DECAY_ENABLED=true
MEMORY_DECAY_ALGORITHM=ebbinghaus
MEMORY_DECAY_BASE_RETENTION=1.0
MEMORY_DECAY_FORGETTING_RATE=0.1
MEMORY_DECAY_REINFORCEMENT_FACTOR=0.3
```
**JSON 配置示例：**
```json
{
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 1.5,
    "reinforcement_factor": 0.3,
    "working_threshold": 0.3,
    "short_term_threshold": 0.6,
    "long_term_threshold": 0.8
  }
}
```
**Python 字典示例：**
```python
config = {
    'intelligent_memory': {
        'enabled': True,
        'initial_retention': 1.0,
        'decay_rate': 1.5,
        'reinforcement_factor': 0.3,
        'working_threshold': 0.3,
        'short_term_threshold': 0.6,
        'long_term_threshold': 0.8
    }
}
```
---

## 6. 性能配置（可选） {#6-performance-configuration-optional}

性能设置控制批处理大小、缓存和搜索参数。

### 记忆管理设置 {#memory-management-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|--------|------|
| `MEMORY_BATCH_SIZE` | integer | 否 | `100` | 单次批处理中处理的记忆数量 |
| `MEMORY_CACHE_SIZE` | integer | 否 | `1000` | 内存中缓存的最大记忆数量 |
| `MEMORY_CACHE_TTL` | integer | 否 | `3600` | 缓存的存活时间（秒） |
| `MEMORY_SEARCH_LIMIT` | integer | 否 | `10` | 记忆搜索返回的最大结果数量 |
| `MEMORY_SEARCH_THRESHOLD` | float | 否 | `0.7` | 记忆搜索的最低相似度阈值（0.0-1.0） |

### Vector Store 设置 {#vector-store-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|--------|------|
| `VECTOR_STORE_BATCH_SIZE` | integer | 否 | `50` | 单次批处理中处理的向量数量 |
| `VECTOR_STORE_CACHE_SIZE` | integer | 否 | `500` | 缓存的最大向量数量 |
| `VECTOR_STORE_INDEX_REBUILD_INTERVAL` | integer | 否 | `86400` | 向量索引重建的时间间隔（秒）（24小时） |

**环境变量示例：**
```env
MEMORY_BATCH_SIZE=100
MEMORY_CACHE_SIZE=1000
MEMORY_CACHE_TTL=3600
MEMORY_SEARCH_LIMIT=10
MEMORY_SEARCH_THRESHOLD=0.7
VECTOR_STORE_BATCH_SIZE=50
VECTOR_STORE_CACHE_SIZE=500
VECTOR_STORE_INDEX_REBUILD_INTERVAL=86400
```
**注意：** 性能设置通常通过环境变量进行配置。这些设置的 JSON 配置可能因实现而异。请查阅特定 API 文档以获取编程配置选项。

---

## 7. 安全配置（可选） {#7-security-configuration-optional}

安全设置用于控制加密和访问控制。

### 加密设置 {#encryption-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `ENCRYPTION_ENABLED` | boolean | 否 | `false` | 启用存储记忆的加密功能 |
| `ENCRYPTION_KEY` | string | 是* | - | 加密密钥。当 `ENCRYPTION_ENABLED=true` 时必需。应为一个安全的随机字符串 |
| `ENCRYPTION_ALGORITHM` | string | 否 | `AES-256-GCM` | 使用的加密算法。选项：`AES-256-GCM` |

### 访问控制设置 {#access-control-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `ACCESS_CONTROL_ENABLED` | boolean | 否 | `true` | 启用记忆的访问控制功能 |
| `ACCESS_CONTROL_DEFAULT_PERMISSION` | string | 否 | `READ_ONLY` | 默认权限级别。选项：`READ_ONLY`，`READ_WRITE`，`FULL` |
| `ACCESS_CONTROL_ADMIN_USERS` | string | 否 | `admin,root` | 管理员用户名的逗号分隔列表 |

**环境变量示例：**
```env
ENCRYPTION_ENABLED=false
ENCRYPTION_KEY=
ENCRYPTION_ALGORITHM=AES-256-GCM
ACCESS_CONTROL_ENABLED=true
ACCESS_CONTROL_DEFAULT_PERMISSION=READ_ONLY
ACCESS_CONTROL_ADMIN_USERS=admin,root
```
**注意：** 安全设置通常通过环境变量进行配置。这些设置的 JSON 配置可能会根据具体实现有所不同。

---

## 8. Telemetry 配置（可选） {#8-telemetry-configuration-optional}

Telemetry 设置用于控制使用分析和监控。

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|--------|------|
| `TELEMETRY_ENABLED` | boolean | 否 | `false` | 启用 Telemetry 数据收集 |
| `TELEMETRY_ENDPOINT` | string | 否 | `https://telemetry.powermem.ai` | Telemetry 端点 URL |
| `TELEMETRY_API_KEY` | string | 是* | - | Telemetry 端点的 API 密钥。当 `TELEMETRY_ENABLED=true` 时必需 |
| `TELEMETRY_BATCH_SIZE` | integer | 否 | `100` | 在发送前批量处理的 Telemetry 事件数量 |
| `TELEMETRY_FLUSH_INTERVAL` | integer | 否 | `30` | Telemetry 刷新间隔（以秒为单位） |
| `TELEMETRY_RETENTION_DAYS` | integer | 否 | `30` | Telemetry 数据保留的天数 |

**环境变量示例：**
```env
TELEMETRY_ENABLED=false
TELEMETRY_ENDPOINT=https://telemetry.powermem.ai
TELEMETRY_API_KEY=
TELEMETRY_BATCH_SIZE=100
TELEMETRY_FLUSH_INTERVAL=30
TELEMETRY_RETENTION_DAYS=30
```
**JSON 配置示例:**
```json
{
  "telemetry": {
    "enable_telemetry": false,
    "telemetry_endpoint": "https://telemetry.powermem.ai",
    "telemetry_api_key": "",
    "telemetry_batch_size": 100,
    "telemetry_flush_interval": 30
  }
}
```
**Python 字典示例:**
```python
config = {
    'telemetry': {
        'enable_telemetry': False,
        'telemetry_endpoint': 'https://telemetry.powermem.ai',
        'telemetry_api_key': '',
        'telemetry_batch_size': 100,
        'telemetry_flush_interval': 30
    }
}
```
---

## 9. 审计配置（可选） {#9-audit-configuration-optional}

审计设置用于控制合规性和安全性的审计日志记录。

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `AUDIT_ENABLED` | boolean | 否 | `true` | 启用审计日志记录 |
| `AUDIT_LOG_FILE` | string | 否 | `./runtime-output/audit.txt` | 审计日志文件路径 |
| `AUDIT_LOG_LEVEL` | string | 否 | `INFO` | 审计日志级别。选项：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `AUDIT_RETENTION_DAYS` | integer | 否 | `90` | 审计日志保留的天数 |
| `AUDIT_COMPRESS_LOGS` | boolean | 否 | `true` | 压缩旧的审计日志文件 |
| `AUDIT_LOG_ROTATION_SIZE` | string | 否 | `100MB` | 审计日志文件在轮换前的最大大小（例如：`100MB`、`1GB`） |

**环境变量示例：**
```env
AUDIT_ENABLED=true
AUDIT_LOG_FILE=./runtime-output/audit.txt
AUDIT_LOG_LEVEL=INFO
AUDIT_RETENTION_DAYS=90
AUDIT_COMPRESS_LOGS=true
AUDIT_LOG_ROTATION_SIZE=100MB
```
**JSON 配置示例：**
```json
{
  "audit": {
    "enabled": true,
    "log_file": "./runtime-output/audit.txt",
    "log_level": "INFO",
    "retention_days": 90
  }
}
```
**Python 字典示例：**
```python
config = {
    'audit': {
        'enabled': True,
        'log_file': './runtime-output/audit.txt',
        'log_level': 'INFO',
        'retention_days': 90
    }
}
```
---

## 10. 日志配置（可选） {#10-logging-configuration-optional}

日志设置控制通用应用程序的日志记录。

### 通用日志设置 {#general-logging-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `LOGGING_LEVEL` | string | 否 | `DEBUG` | 日志级别。选项：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `LOGGING_FORMAT` | string | 否 | `%(asctime)s - %(name)s - %(levelname)s - %(message)s` | 日志消息格式（Python 日志格式） |
| `LOGGING_FILE` | string | 否 | `./runtime-output/powermem.txt` | 日志文件路径 |
| `LOGGING_MAX_SIZE` | string | 否 | `100MB` | 日志文件在轮转前的最大大小 |
| `LOGGING_BACKUP_COUNT` | integer | 否 | `5` | 保留的备份日志文件数量 |
| `LOGGING_COMPRESS_BACKUPS` | boolean | 否 | `true` | 压缩旧的日志文件 |

### 控制台日志设置 {#console-logging-settings}

| 配置项 | 类型 | 是否必需 | 默认值 | 描述 |
|--------|------|----------|---------|------|
| `LOGGING_CONSOLE_ENABLED` | boolean | 否 | `true` | 启用控制台日志记录 |
| `LOGGING_CONSOLE_LEVEL` | string | 否 | `INFO` | 控制台日志级别。选项：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `LOGGING_CONSOLE_FORMAT` | string | 否 | `%(levelname)s - %(message)s` | 控制台日志消息格式 |

**环境变量示例：**
```env
LOGGING_LEVEL=DEBUG
LOGGING_FORMAT=%(asctime)s - %(name)s - %(levelname)s - %(message)s
LOGGING_FILE=./runtime-output/powermem.txt
LOGGING_MAX_SIZE=100MB
LOGGING_BACKUP_COUNT=5
LOGGING_COMPRESS_BACKUPS=true
LOGGING_CONSOLE_ENABLED=true
LOGGING_CONSOLE_LEVEL=INFO
LOGGING_CONSOLE_FORMAT=%(levelname)s - %(message)s
```
**JSON 配置示例：**
```json
{
  "logging": {
    "level": "DEBUG",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": "./runtime-output/powermem.txt"
  }
}
```
**Python 字典示例:**
```python
config = {
    'logging': {
        'level': 'DEBUG',
        'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        'file': './runtime-output/powermem.txt'
    }
}
```
---

## 快速开始示例 {#quick-start-examples}

### 最小开发配置 {#minimal-development-configuration}

**环境变量：**
```env
# 必需：数据库
DATABASE_PROVIDER=sqlite
SQLITE_PATH=./data/powermem_dev.db

# 必需：LLM
LLM_PROVIDER=qwen
LLM_API_KEY=your_api_key_here
LLM_MODEL=qwen-plus

# 必需：Embedding
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_api_key_here
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536
```
**JSON 配置：**
```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db"
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus"
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  }
}
```
**Python 字典：**
```python
config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    }
}

from powermem import Memory
memory = Memory(config=config)
```
### 使用 OceanBase 的生产环境配置 {#production-configuration-with-oceanbase}

**环境变量：**
```env
# 数据库
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=prod-db.example.com
OCEANBASE_PORT=2881
OCEANBASE_USER=prod_user
OCEANBASE_PASSWORD=secure_password
OCEANBASE_DATABASE=powermem_prod
OCEANBASE_EMBEDDING_MODEL_DIMS=1536

# LLM 配置
LLM_PROVIDER=qwen
LLM_API_KEY=production_key
LLM_MODEL=qwen-plus

# Embedding 配置
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=production_key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536

# 可选：启用智能记忆和审计
INTELLIGENT_MEMORY_ENABLED=true
AUDIT_ENABLED=true
```
**JSON 配置：**
```json
{
  "vector_store": {
    "provider": "oceanbase",
    "config": {
      "collection_name": "memories",
      "connection_args": {
        "host": "prod-db.example.com",
        "port": 2881,
        "user": "prod_user",
        "password": "secure_password",
        "db_name": "powermem_prod"
      },
      "embedding_model_dims": 1536,
      "vidx_metric_type": "cosine",
      "index_type": "IVF_FLAT"
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "production_key",
      "model": "qwen-plus"
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "production_key",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  },
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 1.5,
    "reinforcement_factor": 0.3
  },
  "audit": {
    "enabled": true,
    "log_file": "./runtime-output/audit.txt",
    "log_level": "INFO"
  }
}
```
**Python 字典：**
```python
config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': 'prod-db.example.com',
                'port': 2881,
                'user': 'prod_user',
                'password': 'secure_password',
                'db_name': 'powermem_prod'
            },
            'embedding_model_dims': 1536,
            'vidx_metric_type': 'cosine',
            'index_type': 'IVF_FLAT'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'production_key',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'production_key',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    },
    'intelligent_memory': {
        'enabled': True,
        'initial_retention': 1.0,
        'decay_rate': 1.5,
        'reinforcement_factor': 0.3
    },
    'audit': {
        'enabled': True,
        'log_file': './runtime-output/audit.txt',
        'log_level': 'INFO'
    }
}

from powermem import Memory
memory = Memory(config=config)
```
### 完整配置示例（JSON） {#complete-configuration-example-json}

以下是一个包含所有可选设置的完整 JSON 配置文件示例（`config.json`）：
```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db",
      "enable_wal": true,
      "timeout": 30
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus",
      "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1",
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 0.8,
      "top_k": 50,
      "enable_search": false
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  },
  "agent_memory": {
    "enabled": true,
    "mode": "auto",
    "default_scope": "AGENT",
    "default_privacy_level": "PRIVATE",
    "default_collaboration_level": "READ_ONLY",
    "default_access_permission": "OWNER_ONLY"
  },
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 1.5,
    "reinforcement_factor": 0.3,
    "working_threshold": 0.3,
    "short_term_threshold": 0.6,
    "long_term_threshold": 0.8
  },
  "telemetry": {
    "enable_telemetry": false,
    "telemetry_endpoint": "https://telemetry.powermem.ai",
    "telemetry_api_key": "",
    "telemetry_batch_size": 100,
    "telemetry_flush_interval": 30
  },
  "audit": {
    "enabled": true,
    "log_file": "./runtime-output/audit.txt",
    "log_level": "INFO",
    "retention_days": 90
  },
  "logging": {
    "level": "DEBUG",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": "./runtime-output/powermem.txt"
  }
}
```
**从 JSON 文件加载：**
```python
import json
from json import dump, dumps
from powermem import Memory

# 从 JSON 文件加载配置
with open('config.json', 'r') as f:
    config = json.load(f)

# 创建 Memory 实例
memory = Memory(config=config)
```
---

## 通过环境变量自定义 Prompts {#custom-prompts-via-environment-variables}

可以通过环境变量覆盖三个核心管道的 Prompts，而无需更改任何代码。这对于部署环境或快速实验非常有用。

| 环境变量 | SDK 配置键 | 描述 |
|---|---|---|
| `POWERMEM_CUSTOM_FACT_EXTRACTION_PROMPT` | `custom_fact_extraction_prompt` | 覆盖默认的事实/记忆提取 Prompt |
| `POWERMEM_CUSTOM_UPDATE_MEMORY_PROMPT` | `custom_update_memory_prompt` | 覆盖默认的记忆更新决策 Prompt |
| `POWERMEM_CUSTOM_IMPORTANCE_EVALUATION_PROMPT` | `custom_importance_evaluation_prompt` | 覆盖默认的重要性评分 Prompt |

**示例 `.env`:**
```bash
POWERMEM_CUSTOM_FACT_EXTRACTION_PROMPT=You are an information extraction expert. Output JSON: {"facts": [...]}
```
**用法：**
```python
from powermem import create_memory

# 从环境变量自动加载 Prompt
memory = create_memory()
```
如果未设置环境变量，系统将回退到内置的默认 prompt。有关详细的 prompt 示例和指南，请参阅[自定义 prompt 使用指南](0004-custom_prompts_usage.md)。

---
