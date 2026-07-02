# 稀疏向量指南 {#sparse-vector-guide}

本指南解释了如何在 PowerMem 中使用稀疏向量功能，包括配置、查询使用、模式升级和历史数据迁移。

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem (`pip install powermem`)
- 数据库要求：**seekdb** 或 **OceanBase >= 4.5.0**

> **注意**: 稀疏向量功能仅支持 OceanBase 存储后端，SQLite 不支持此功能。

## 配置稀疏向量 {#configuring-sparse-vector}

要启用稀疏向量功能，需要配置以下两个部分：

1. `vector_store.config.include_sparse = True` - 启用稀疏向量支持
2. `sparse_embedder` - 配置稀疏向量嵌入服务

### 环境变量配置 {#environment-variable-configuration}

将以下配置添加到您的 `.env` 文件中：
```env
# 数据库配置
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=localhost
OCEANBASE_PORT=2881
OCEANBASE_USER=root
OCEANBASE_PASSWORD=your_password
OCEANBASE_DATABASE=powermem
OCEANBASE_COLLECTION=memories
OCEANBASE_EMBEDDING_MODEL_DIMS=1536

# 启用稀疏向量
SPARSE_VECTOR_ENABLE=true

# 稀疏向量 Embedding 配置
SPARSE_EMBEDDER_PROVIDER=qwen
SPARSE_EMBEDDER_API_KEY=your_api_key
SPARSE_EMBEDDER_MODEL=text-embedding-v4
SPARSE_EMBEDDER_DIMS=1536
```
### 字典配置 {#dictionary-configuration}

使用 Python 字典配置稀疏向量：
```python
from powermem import Memory

config = {
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
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    },
    # 稀疏向量 Embedding 配置
    'sparse_embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'embedding_model_dims': 1536,
            'include_sparse': True,  # 启用稀疏向量
            'connection_args': {
                'host': 'localhost',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            },
            # 可选：配置搜索权重
            'vector_weight': 0.5,
            'fts_weight': 0.5,
            'sparse_weight': 0.25
        }
    }
}

memory = Memory(config=config)
```
### 配置参数 {#configuration-parameters}

| 参数 | 类型 | 默认值 | 描述 |
|------|------|-------|------|
| `include_sparse` | bool | `False` | 是否启用稀疏向量支持 |
| `sparse_embedder.provider` | string | - | 稀疏向量嵌入提供商（当前支持 `qwen`） |
| `sparse_embedder.config.api_key` | string | - | API 密钥 |
| `sparse_embedder.config.model` | string | - | 嵌入模型名称 |
| `vector_weight` | float | `0.5` | 向量搜索权重 |
| `fts_weight` | float | `0.5` | 全文搜索权重 |
| `sparse_weight` | float | `0.25` | 稀疏向量搜索权重 |

## 查询使用 {#query-usage}

配置稀疏向量后，搜索将自动使用稀疏向量进行混合搜索，无需任何代码更改。

### 基本搜索 {#basic-search}
```python
from powermem import Memory, auto_config

# 加载配置（自动从 .env 加载）
config = auto_config()
memory = Memory(config=config)

# 添加记忆（自动生成稀疏向量）
memory.add(
    messages="Machine learning is a branch of artificial intelligence, I love machine learning",
    user_id="user123"
)

# 搜索（自动使用稀疏向量进行混合搜索）
results = memory.search(
    query="AI technology",
    user_id="user123",
    limit=10
)
```
### 搜索权重配置 {#search-weight-configuration}

搜索结合了三种方法：向量搜索、全文搜索和稀疏向量搜索。您可以通过配置权重来调整每种搜索方法的影响力：

- `vector_weight`：向量搜索权重（默认值 0.5）
- `fts_weight`：全文搜索权重（默认值 0.5）
- `sparse_weight`：稀疏向量搜索权重（默认值 0.25）

## 模式升级和数据迁移 {#schema-upgrade-and-data-migration}

如果您已有一个不支持稀疏向量的表，则需要升级模式，并可选择性地迁移历史数据。

有关升级现有表和迁移历史数据的详细说明，请参阅：

**[稀疏向量迁移指南](../migration/sparse_vector_migration.md)**

迁移指南涵盖：
- 模式升级步骤
- 迁移参数和选项
- 进度监控
- 验证方法
- 回滚流程

## 完整使用流程 {#complete-usage-workflow}

### 新表（推荐） {#new-table-recommended}

如果创建新表，只需在配置中启用稀疏向量：
```python
from powermem import Memory, auto_config

config = auto_config()  # 确保配置中 include_sparse=True
memory = Memory(config=config)

# 添加记忆（自动生成稀疏向量）
memory.add(messages="memory content", user_id="user123")

# 搜索（自动使用稀疏向量）
results = memory.search(query="query content", user_id="user123")
```
### 现有表升级 {#existing-table-upgrade}

如果需要升级现有表，请参考 [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md) 获取详细说明。