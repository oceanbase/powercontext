# 场景 10：稀疏向量 {#scenario-10-sparse-vector}

本示例演示如何使用稀疏向量功能，包括配置、添加记忆、搜索，以及升级现有表和迁移历史数据。

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem（`pip install powermem`）
- 数据库：**seekdb** 或 **OceanBase >= 4.5.0**

## 第一步：配置稀疏向量 {#step-1-configure-sparse-vector}

创建配置以启用稀疏向量支持：
```python
# sparse_vector_example.py
from powermem import Memory

config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'qwen-plus',
            'temperature': 0.2
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
            'collection_name': 'sparse_demo',
            'embedding_model_dims': 1536,
            'include_sparse': True,  # 启用稀疏向量
            'connection_args': {
                'host': 'localhost',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            }
        }
    }
}

print("✓ Configuration created successfully")
```
**运行代码：**
```bash
python sparse_vector_example.py
```
**预期输出：**
```
✓ Configuration created successfully
```
## 第2步：初始化记忆 {#step-2-initialize-memory}

使用配置创建一个Memory实例。对于新表，系统将自动创建支持稀疏向量的表结构：
```python
# sparse_vector_example.py
from powermem import Memory

# ... 配置代码（同第 1 步）

# 创建 Memory 实例
memory = Memory(config=config)

print("✓ Memory initialized successfully")
print(f"  - sparse_embedder: {memory.sparse_embedder is not None}")
```
**预期输出：**
```
✓ Memory initialized successfully
  - sparse_embedder: True
```
## 第 3 步：添加记忆 {#step-3-add-memories}

在添加记忆时，系统会自动生成稀疏向量：
```python
# sparse_vector_example.py
from powermem import Memory

# ... 初始化代码（同第 2 步）

# 添加测试记忆
test_memories = [
    "Machine learning is a branch of artificial intelligence, focusing on algorithms and statistical models",
    "Natural language processing is an interdisciplinary field of computer science and artificial intelligence",
    "Vector search is an important technology for information retrieval, used for similarity matching",
    "Deep learning uses multi-layer neural networks for feature learning",
    "Knowledge graphs are graph-structured data used to represent entities and their relationships"
]

print("Adding test memories...")
for content in test_memories:
    memory.add(
        messages=content,
        user_id="user123",
        infer=False  # 不使用智能推理，直接存储
    )

print(f"✓ Successfully added {len(test_memories)} memories")
```
**预期输出：**
```
Adding test memories...
✓ Successfully added 5 memories
```
## 第4步：执行搜索 {#step-4-execute-search}

搜索将自动使用稀疏向量进行混合搜索：
```python
# sparse_vector_example.py
from powermem import Memory

# ... 添加记忆代码（同第 3 步）

# 执行搜索
query = "AI algorithms"
print(f"\nSearch query: '{query}'")

results = memory.search(
    query=query,
    user_id="user123",
    limit=5
)

# 显示搜索结果
print(f"Found {len(results.get('results', []))} results:\n")
for i, result in enumerate(results.get('results', []), 1):
    print(f"{i}. Score: {result['score']:.4f}")
    print(f"   Content: {result['memory'][:50]}...")
    print()
```
## 第五步：升级现有表的Schema（如有需要） {#step-5-upgrade-schema-for-existing-table-if-needed}

如果您已有一个不支持Sparse Vector的表，则需要升级其Schema，并可选择性地迁移历史数据。

有关升级现有表和迁移历史数据的详细说明，请参阅：

**[Sparse Vector迁移指南](../migration/sparse_vector_migration.md)**

迁移指南涵盖以下内容：
- Schema升级流程
- 历史数据迁移
- 迁移参数和选项
- 进度监控与验证
- 回滚步骤

快速示例：
```python
from powermem import auto_config
from script import ScriptManager

# 加载配置
config = auto_config()

# 运行升级脚本
ScriptManager.run('upgrade-sparse-vector', config)
```
## 完整示例代码 {#complete-example-code}

以下是一个完整的使用示例：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整稀疏向量示例
演示如何使用稀疏向量功能
"""
from powermem import Memory, auto_config

def main():
    # 加载配置（已启用稀疏向量）
    config = auto_config()

    # 创建 Memory 实例
    memory = Memory(config=config)

    # 添加测试记忆（自动生成稀疏向量）
    test_memories = [
        "Machine learning is a branch of artificial intelligence, focusing on algorithms and statistical models",
        "Natural language processing is an interdisciplinary field of computer science and artificial intelligence",
        "Vector search is an important technology for information retrieval, used for similarity matching",
    ]

    print("Adding test memories...")
    for content in test_memories:
        memory.add(
            messages=content,
            user_id="user123",
            infer=False
        )

    # 搜索（自动使用稀疏向量进行混合搜索）
    print("\nSearching...")
    results = memory.search(
        query="AI technology",
        user_id="user123",
        limit=5
    )

    # 显示结果
    print(f"\nFound {len(results.get('results', []))} results:")
    for i, result in enumerate(results.get('results', []), 1):
        print(f"{i}. Score: {result['score']:.4f}")
        print(f"   Content: {result['memory'][:80]}...")
        print()

if __name__ == "__main__":
    main()
```
> **注意**：有关升级现有表和迁移历史数据，请参考 [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md)。

## 拓展练习 {#extended-exercises}

1. **尝试不同的搜索权重**：修改 `vector_weight`、`fts_weight` 和 `sparse_weight` 参数，观察搜索结果的变化。

2. **比较搜索效果**：分别测试启用和禁用 Sparse Vector 的搜索结果，并比较相关性。

## 相关文档 {#related-documentation}

- [Sparse Vector Guide](../guides/0011-sparse_vector.md) - 详细的 Sparse Vector 配置指南
- [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md) - 模式升级和数据迁移指南
- [Configuration Guide](../guides/0003-configuration.md) - 完整的配置参考
- [Getting Started](../guides/0001-getting_started.md) - 快速入门指南