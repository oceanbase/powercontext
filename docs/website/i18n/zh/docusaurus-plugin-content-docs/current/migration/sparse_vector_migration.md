# 稀疏向量迁移指南 {#sparse-vector-migration-guide}

本指南提供了关于如何为现有表添加稀疏向量支持以及迁移历史数据的详细说明。

## 前置条件 {#prerequisites}

- Python 3.10+
- 已安装 powermem（`pip install powermem`）
- 数据库要求：**seekdb** 或 **OceanBase >= 4.5.0**
- 不支持稀疏向量的现有表

> **注意**: 对于新表，只需在配置中启用 `include_sparse=True`。无需进行升级或迁移操作。

## 迁移工作流概览 {#migration-workflow-overview}
```
Existing Table (without sparse vector)
    ↓
1. Configure sparse vector
    ↓
2. Run Schema upgrade script (required)
    ↓
3. Run data migration script (optional, but recommended)
    ↓
4. Verify migration results
```
## 第 1 步：配置 Sparse Vector {#step-1-configure-sparse-vector}

在运行升级脚本之前，您需要配置 Sparse Vector。将以下配置添加到您的 `.env` 文件中：
```env
# 启用稀疏向量
SPARSE_VECTOR_ENABLE=true

# 稀疏向量 Embedding 配置
SPARSE_EMBEDDER_PROVIDER=qwen
SPARSE_EMBEDDER_API_KEY=your_api_key
SPARSE_EMBEDDER_MODEL=text-embedding-v4
SPARSE_EMBEDDER_DIMS=1536
```
或者使用字典配置：
```python
config = {
    # ... 其他配置 ...
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
            'include_sparse': True,  # 启用稀疏向量
            # ... 其他配置 ...
        }
    }
}
```
## 第2步：模式升级（必需） {#step-2-schema-upgrade-required}

### 列出可用脚本 {#list-available-scripts}
```python
from script import ScriptManager

# 列出所有可用脚本
ScriptManager.list_scripts()
```
示例输出：
```
======================================================================
PowerMem Available Scripts
======================================================================

【Upgrade Scripts - Add new features or upgrade existing features】
----------------------------------------------------------------------
  • upgrade-sparse-vector
    Add sparse vector support to OceanBase table (add sparse_embedding column and index) (requires: dict)

======================================================================
```
### 查看脚本详情 {#view-script-details}
```python
from script import ScriptManager

# 查看升级脚本详情
ScriptManager.info('upgrade-sparse-vector')
```
示例输出：
```
======================================================================
Script: upgrade-sparse-vector
======================================================================
Category: upgrade
Description: Add sparse vector support to OceanBase table (add sparse_embedding column and index)

----------------------------------------------------------------------
Parameters:
----------------------------------------------------------------------
  config (dict) (required)
```
### 执行升级脚本 {#execute-upgrade-script}
```python
from powermem import auto_config
from script import ScriptManager

# 加载配置
config = auto_config()

# 运行升级脚本
ScriptManager.run('upgrade-sparse-vector', config)
```
**预期输出：**

迁移到 Sparse Vector 后，您应该能够观察到以下结果：

1. **更高效的查询性能**：Sparse Vector 提供了更快的查询速度，特别是在处理大规模数据集时。
2. **更低的存储需求**：通过稀疏表示法，Sparse Vector 显著减少了存储空间的占用。
3. **兼容性**：Sparse Vector 与现有的 Vector Store 和 GraphRAG 集成无缝衔接。
4. **灵活性**：支持多种 LLM 和 Embedding 模型，适应不同的用例需求。

如果您在迁移过程中遇到任何问题，请回到本迁移文档的检查步骤，或联系技术支持。
```
Preparing to execute script: upgrade-sparse-vector
Description: Add sparse vector support to OceanBase table (add sparse_embedding column and index)
Loading module: script.scripts.upgrade_sparse_vector
Executing script function: upgrade_sparse_vector
Starting sparse vector upgrade for table 'memories'
Adding sparse_embedding column to table 'memories'
sparse_embedding column added successfully
Creating sparse vector index on table 'memories'
sparse_embedding_idx created successfully
Sparse vector upgrade completed successfully for table 'memories'

✓ Script 'upgrade-sparse-vector' executed successfully!
```
### 升级脚本执行的操作 {#operations-performed-by-upgrade-script}

升级脚本执行以下操作：
1. 检查数据库版本是否支持稀疏向量
2. 添加 `sparse_embedding` 列（SPARSE_VECTOR 类型）
3. 创建 `sparse_embedding_idx` 索引

> **注意**: 升级脚本是幂等的，可以安全地多次执行。

## 第三步：历史数据迁移（可选，但推荐） {#step-3-historical-data-migration-optional-but-recommended}

在模式升级后，历史数据的 `sparse_embedding` 列为空。**历史数据迁移不是必须的**，但强烈建议运行迁移脚本，原因如下：

- **只有迁移的数据会参与稀疏向量检索**：未迁移的历史数据在搜索时不会使用稀疏向量。只有新添加的数据和已迁移的数据会参与稀疏向量搜索
- **迁移后结果更准确**：稀疏向量搜索提供更精确的语义匹配。迁移历史数据后，所有数据都能受益于稀疏向量带来的搜索精度提升
- **新数据会自动生成**：即使不迁移历史数据，新添加的数据也会自动生成稀疏向量并参与搜索

### 查看迁移脚本详情 {#view-migration-script-details}
```python
from script import ScriptManager

# 查看迁移脚本详情
ScriptManager.info('migrate-sparse-vector')
```
### 迁移参数 {#migration-parameters}

| 参数          | 类型  | 默认值   | 描述                                   |
|---------------|-------|---------|----------------------------------------|
| `batch_size`  | int   | `100`   | 每批处理的记录数量                     |
| `workers`     | int   | `1`     | 并发线程数，增加此值可提高迁移速度     |
| `delay`       | float | `0.1`   | 批次之间的延迟时间（秒）               |
| `dry_run`     | bool  | `False` | 测试模式，仅处理 100 条记录且不写入数据库 |

### 使用 dry-run 模式进行测试 {#test-with-dry-run-mode}

在正式迁移之前，建议先使用 dry-run 模式进行测试：
```python
from powermem import Memory, auto_config
from script import ScriptManager

# 加载配置
config = auto_config()

# 创建 Memory 实例（迁移脚本需要 Memory 实例）
memory = Memory(config=config)

# 测试模式（仅处理 100 条记录，不写入数据库）
print("Test mode (dry-run):")
ScriptManager.run('migrate-sparse-vector', memory, dry_run=True)
```
**预期输出：**

迁移到 Sparse Vector 后，您应该能够观察到以下结果：

1. **性能提升**：通过利用 Sparse Vector 的稀疏性，查询速度将显著提高，尤其是在处理大规模数据集时。
2. **存储优化**：Sparse Vector 允许更高效地存储数据，从而减少存储需求。
3. **兼容性**：现有的 MemoryItem 和 UserMemory 数据结构将无缝集成到新的 Sparse Vector 架构中。
4. **灵活性**：支持更复杂的查询和过滤操作，同时保持高效的性能表现。

如果您在迁移过程中遇到任何问题，请回到本迁移文档的检查步骤，或联系技术支持团队。
```
Test mode (dry-run):
Preparing to execute script: migrate-sparse-vector
...
[DRY RUN] Mode enabled - will only test with 100 records

Total: [██████████████]  100.0% | 100/100
  ✓ Migrated: 100 | ✗ Failed: 0
  ⏱ Elapsed: 5.2s | Remaining: ~0s | 📊 19.2 rec/s

✓ Script 'migrate-sparse-vector' executed successfully!
```
### 执行正式迁移 {#execute-formal-migration}
```python
from powermem import Memory, auto_config
from script import ScriptManager

# 加载配置
config = auto_config()

# 创建 Memory 实例
memory = Memory(config=config)

# 正式迁移（建议配置并发线程以提升速度）
print("Formal migration:")
ScriptManager.run('migrate-sparse-vector', memory, batch_size=100, workers=3)
```
**预期输出：**

在迁移到 Sparse Vector 存储后，您的系统应该能够：

1. 更高效地处理大规模数据集。
2. 提供更快的查询性能，特别是在高维度数据的情况下。
3. 减少存储空间的使用，同时保持数据的准确性。
4. 与现有的 Vector Store 集成，支持混合查询。
5. 利用 Sparse Vector 的稀疏性优化计算资源。

如果您的系统未能达到上述预期，请检查以下内容：

- 确保所有相关的配置已正确更新。
- 验证 Sparse Vector 存储是否已正确初始化。
- 检查是否有任何与 Sparse Vector 不兼容的查询模式。
```
Formal migration:
Preparing to execute script: migrate-sparse-vector
...
Total records to migrate: 10000
Batch size: 100
Thread pool size: 3

Total: [████████████░░]  85.0% | 8,500/10,000
  ✓ Migrated: 8,500 | ✗ Failed: 0
  ⏱ Elapsed: 3m 42s | Remaining: ~39s | 📊 38.3 rec/s

Workers (3):
  Worker 0: ✓ 2,834 | ✗ 0
  Worker 1: ✓ 2,833 | ✗ 0
  Worker 2: ✓ 2,833 | ✗ 0
```
### 迁移进度 {#migration-progress}

迁移过程中将实时显示进度：
```
Total: [████████░░░░░░] 57.1% | 5,710/10,000
  ✓ Migrated: 5,710 | ✗ Failed: 0
  ⏱ Elapsed: 2m 30s | Remaining: ~1m 52s | 📊 38.1 rec/s

Workers (3):
  Worker 0: ✓ 1,903 | ✗ 0
  Worker 1: ✓ 1,904 | ✗ 0
  Worker 2: ✓ 1,903 | ✗ 0
```
进度信息包括：
- **进度条**：显示完成百分比和数量
- **已迁移/失败**：成功和失败记录的数量
- **已用时间/剩余时间**：已用时间和预计剩余时间
- **速度**：每秒处理的记录数
- **工作线程**：每个线程的处理状态

## 第4步：验证迁移结果 {#step-4-verify-migration-results}

迁移完成后，验证稀疏向量是否正常工作：
```python
from powermem import Memory, auto_config
import logging

# 加载配置
config = auto_config()
memory = Memory(config=config)

# 启用 DEBUG 日志以查看搜索详情
logging.getLogger().setLevel(logging.DEBUG)

# 执行搜索
print("Executing verification search...")
result = memory.search(query="test query", limit=10)

print(f"\n✓ Search returned {len(result.get('results', []))} results")
print("  Sparse vector search is active (check DEBUG logs to confirm)")
```
**预期输出：**

迁移到 Sparse Vector 后，您的系统应能够支持以下功能：

1. **高效的向量检索：**
   Sparse Vector 提供了优化的向量检索能力，能够快速处理大规模数据集。

2. **兼容性：**
   Sparse Vector 与现有的 Memory 和 Vector Store 集成无缝，支持多种 LLM 和 Embedding 模型。

3. **灵活性：**
   通过 GraphRAG 和 Sub Store，您可以根据需求自定义数据存储和检索策略。

4. **性能提升：**
   在 OceanBase 或其他数据库上运行时，Sparse Vector 能显著提升查询性能。

5. **扩展性：**
   支持分布式部署，适用于大规模 Multi-Agent 系统。

迁移完成后，请通过以下步骤验证系统功能：

- **运行测试用例：**
  使用提供的测试脚本验证 Sparse Vector 的检索和存储功能。

- **检查兼容性：**
  确保与现有的 API 和 CLI 工具无缝集成。

- **监控性能：**
  使用 MCP 或其他监控工具评估性能改进情况。
```
Executing verification search...
DEBUG:powermem.storage.oceanbase.oceanbase:Executing sparse vector search query with sparse_vector: ...
DEBUG:powermem.storage.oceanbase.oceanbase:_sparse_search results, len : 10

✓ Search returned 10 results
  Sparse vector search is active (check DEBUG logs to confirm)
```
您可以在 DEBUG 日志中查看与稀疏向量搜索相关的信息。

## 完整迁移示例 {#complete-migration-example}
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整稀疏向量迁移示例
演示如何升级现有表并迁移历史数据
"""
from powermem import Memory, auto_config
from script import ScriptManager
import logging

def main():
    # 1. 列出可用脚本
    print("=" * 60)
    print("Step 1: List Available Scripts")
    print("=" * 60)
    ScriptManager.list_scripts()

    # 2. 查看脚本详情
    print("\n" + "=" * 60)
    print("Step 2: View Script Details")
    print("=" * 60)
    ScriptManager.info("upgrade-sparse-vector")
    ScriptManager.info("migrate-sparse-vector")

    # 3. 加载配置
    config = auto_config()

    # 4. 运行升级脚本（为现有表添加稀疏向量支持）
    print("\n" + "=" * 60)
    print("Step 3: Run Schema Upgrade Script")
    print("=" * 60)
    ScriptManager.run('upgrade-sparse-vector', config)

    # 5. 创建 Memory 实例
    memory = Memory(config=config)

    # 6. 测试迁移（dry-run 模式）
    print("\n" + "=" * 60)
    print("Step 4: Test Migration (dry-run)")
    print("=" * 60)
    ScriptManager.run('migrate-sparse-vector', memory, dry_run=True)

    # 7. 正式迁移（可选：为历史数据生成稀疏向量）
    # 注意：只有已迁移数据会参与稀疏向量检索，迁移后结果更准确
    print("\n" + "=" * 60)
    print("Step 5: Run Data Migration Script (Optional)")
    print("=" * 60)
    user_input = input("Execute formal migration? (y/N): ")
    if user_input.lower() == 'y':
        ScriptManager.run('migrate-sparse-vector', memory, batch_size=100, workers=3)

    # 8. 验证搜索
    print("\n" + "=" * 60)
    print("Step 6: Verify Search")
    print("=" * 60)
    logging.getLogger().setLevel(logging.DEBUG)
    result = memory.search(query="test query", limit=10)
    print(f"Search returned {len(result.get('results', []))} results")

if __name__ == "__main__":
    main()
```
## 回滚（可选） {#rollback-optional}

如果需要移除 Sparse Vector 支持，可以运行降级脚本：
```python
from powermem import auto_config
from script import ScriptManager

config = auto_config()

# 运行降级脚本（会删除所有稀疏向量数据）
ScriptManager.run('downgrade-sparse-vector', config)
```
> **警告**: 降级脚本将删除 `sparse_embedding` 列和索引。所有稀疏向量数据将被永久删除！

## 常见问题解答 {#frequently-asked-questions}

### 1. 是否需要迁移历史数据？ {#1-is-historical-data-migration-required}

不需要，但强烈推荐。未迁移的历史数据：
- 不会参与稀疏向量检索
- 仍然可以通过向量搜索和全文搜索找到
- 新增数据将自动生成稀疏向量

### 2. 如何提高迁移速度？ {#2-how-to-improve-migration-speed}

- 增加 `workers` 参数值（并发线程数）
- 调整 `batch_size`（批量大小）
- 减少 `delay`（批次之间的延迟）

### 3. 如果迁移失败怎么办？ {#3-what-to-do-if-migration-fails}

- 检查网络连接和 API 密钥
- 查看详细错误日志
- 可以重新运行迁移脚本。脚本会自动跳过已迁移的数据

### 4. 升级脚本可以多次执行吗？ {#4-can-the-upgrade-script-be-executed-multiple-times}

可以。升级脚本是幂等的，重复执行不会引发问题。

## 相关文档 {#related-documentation}

- [稀疏向量指南](../guides/0011-sparse_vector.md) - 详细的稀疏向量配置指南
- [配置指南](../guides/0003-configuration.md) - 完整的配置参考
- [快速入门](../guides/0001-getting_started.md) - 快速入门指南
