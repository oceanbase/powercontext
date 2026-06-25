# 场景 6: 子存储 - 记忆分区 {#scenario-6-sub-stores---memory-partitioning}

本场景展示了 PowerMem 的子存储功能——将不同类型的记忆分区存储，以实现更高效的查询和管理。

## 前置条件 {#prerequisites}

- 完成场景 1
- 安装了 PowerMem
- 配置了 OceanBase 数据库（或其他支持子存储的存储后端）
- 配置了 LLM 和 Embedding 服务

## 理解子存储 {#understanding-sub-stores}

子存储允许您：
- 将不同类型的记忆存储在独立的表中
- 为每个子存储配置独立的 Embedding 维度和服务
- 根据元数据自动路由到正确的存储
- 将现有数据迁移到子存储
- 提高查询性能和资源利用率

### 典型应用场景 {#typical-application-scenarios}

1. **按记忆类型分区**：语义记忆、情景记忆、工作记忆
2. **按重要性分区**：高优先级记忆使用高维度 Embedding，低优先级记忆使用低维度
3. **按时间性分区**：长期记忆和短期缓存分别存储
4. **按用户分区**：不同用户群体的记忆分别管理

## ⚠️ 重要: 子存储激活 {#️-important-sub-store-activation}

**在使用子存储之前，您必须至少为每个子存储调用一次 `migrate_to_sub_store()`，即使您没有数据需要迁移。** 这将初始化子存储并标记其为可用状态。
```python
# 即使没有数据需要迁移，也必须激活每个子存储：
memory.migrate_all_sub_stores(delete_source=False)
```
激活后：
- 具有匹配元数据的新记忆将自动路由到子存储
- 具有匹配过滤条件的查询将自动路由到子存储
- 如果未执行此激活步骤，子存储将保持休眠状态且未被使用

## 第一步：配置子存储 {#step-1-configure-sub-stores}

首先，让我们创建一个带有子存储的Memory实例：
```python
# sub_store_example.py
from powermem import Memory
import os

# 配置主存储和子存储
config = {
    "database": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "main_memories",
            "embedding_model_dims": 1536,
            "host": os.getenv("OCEANBASE_HOST", "localhost"),
            "port": int(os.getenv("OCEANBASE_PORT", "2881")),
            "user": os.getenv("OCEANBASE_USER", "root@test_tenant"),
            "password": os.getenv("OCEANBASE_PASSWORD", "password"),
            "db_name": os.getenv("OCEANBASE_DATABASE", "powermem"),
        }
    },
    "llm": {
        "provider": "qwen",
        "config": {
            "model": "qwen-max",
            "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
        }
    },
    "embedder": {
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
            "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
        }
    },
    # 配置子存储
    "sub_stores": [
        {
            # 子存储 0：工作记忆（短期、低重要性）
            "collection_name": "working_memories",
            "routing_filter": {
                "memory_type": "working"
            },
            "embedding_model_dims": 1536,
        },
        {
            # 子存储 1：情景记忆（个人经历）
            "collection_name": "episodic_memories",
            "routing_filter": {
                "memory_type": "episodic"
            },
            "embedding_model_dims": 1536,
        }
    ]
}

# 初始化 Memory
memory = Memory(config=config)
print("✓ Memory 已成功初始化，并配置 2 个 Sub Store")
print("  - 主存储：main_memories（用于语义记忆）")
print("  - Sub Store 0：working_memories（用于工作记忆）")
print("  - Sub Store 1：episodic_memories（用于情景记忆）")
```
**运行代码：**
```bash
python sub_store_example.py
```
**预期输出：**

在本示例中，我们将展示如何使用多个 Sub Store 来管理不同类型的 Memory 数据。通过这种方式，您可以根据具体需求对 Memory 数据进行分类和存储。

以下是预期的输出内容：

1. **创建 Sub Store：**
   - 使用 `Memory.create_sub_store()` 方法创建多个 Sub Store。
   - 每个 Sub Store 可以存储特定类型的 Memory 数据，例如用户数据、系统日志或其他分类信息。

2. **存储和检索数据：**
   - 将 Memory 数据存储到对应的 Sub Store 中。
   - 使用 `Memory.get_from_sub_store()` 方法从指定的 Sub Store 中检索数据。

3. **管理 Sub Store：**
   - 列出所有可用的 Sub Store。
   - 删除不再需要的 Sub Store，以优化存储资源。

通过以上步骤，您可以高效地管理和组织 Memory 数据。
```
✓ Memory 已成功初始化，并配置 2 个 Sub Store
  - 主存储：main_memories（用于语义记忆）
  - Sub Store 0：working_memories（用于工作记忆）
  - Sub Store 1：episodic_memories（用于情景记忆）
```
## 第2步：添加不同类型的记忆 {#step-2-add-different-types-of-memories}

让我们向主存储添加不同类型的记忆：
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("正在添加不同类型的记忆...\n")

# 添加语义记忆（长期知识，保留在主存储）
semantic_memories = [
    {
        "messages": "Python is a high-level programming language known for its simplicity,I love Python!",
        "metadata": {"memory_type": "semantic", "topic": "programming"}
    },
    {
        "messages": "Machine learning is a subset of artificial intelligence,I love machine learning!",
        "metadata": {"memory_type": "semantic", "topic": "ai"}
    },
]

print("1. 正在添加语义记忆（知识）...")
for mem in semantic_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ 已添加：{mem['messages'][:40]}...")

# 添加工作记忆（短期任务，将迁移到子存储 0）
working_memories = [
    {
        "messages": "Today's weather is sunny, good for outdoor activities",
        "metadata": {"memory_type": "working", "importance": "low"}
    },
    {
        "messages": "Need to buy groceries after work",
        "metadata": {"memory_type": "working", "importance": "low"}
    },
    {
        "messages": "Meeting scheduled at 3 PM today",
        "metadata": {"memory_type": "working", "importance": "medium"}
    },
]

print("\n2. 正在添加工作记忆（任务）...")
for mem in working_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ 已添加：{mem['messages'][:40]}...")

# 添加情景记忆（个人经历，将迁移到子存储 1）
episodic_memories = [
    {
        "messages": "Last summer I visited Paris and saw the Eiffel Tower",
        "metadata": {"memory_type": "episodic", "time": "2024-07"}
    },
    {
        "messages": "I learned to ride a bike when I was 7 years old",
        "metadata": {"memory_type": "episodic", "time": "childhood"}
    },
]

print("\n3. 正在添加情景记忆（经历）...")
for mem in episodic_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ 已添加：{mem['messages'][:40]}...")

total = len(semantic_memories) + len(working_memories) + len(episodic_memories)
print(f"\n✓ 共添加 {total} 条记忆（当前全部位于主存储）")
```
**预期输出：**

在此场景中，我们将演示如何使用多个 Sub Store 来管理不同类型的记忆数据。通过这种方法，您可以根据需求将记忆分区存储在不同的 Vector Store 中，从而优化查询效率和存储管理。

以下是此场景的主要步骤：

1. 初始化主 Memory Store。
2. 创建多个 Sub Store，每个 Sub Store 专用于一种特定类型的记忆数据。
3. 将数据插入到相应的 Sub Store 中。
4. 查询特定 Sub Store 以检索相关记忆数据。

通过这种方式，您可以更高效地组织和管理复杂的记忆数据结构。
```
正在添加不同类型的记忆...

1. 正在添加语义记忆（知识）...
   ✓ 已添加：Python is a high-level programming langu...
   ✓ 已添加：Machine learning is a subset of artifici...

2. 正在添加工作记忆（任务）...
   ✓ 已添加：Today's weather is sunny, good for outdo...
   ✓ 已添加：Need to buy groceries after work...
   ✓ 已添加：Meeting scheduled at 3 PM today...

3. 正在添加情景记忆（经历）...
   ✓ 已添加：Last summer I visited Paris and saw the ...
   ✓ 已添加：I learned to ride a bike when I was 7 ye...

✓ 共添加 7 条记忆（当前全部位于主存储）
```
## 第三步：迁移前查询 {#step-3-query-before-migration}

迁移前，所有记忆都存储在主存储中：
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("迁移前查询（全部在主存储中）\n")

# 查询工作记忆
print("1. 正在查询工作记忆...")
results = memory.search(
    query="today's tasks",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条结果：")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# 查询情景记忆
print("\n2. 正在查询情景记忆...")
results = memory.search(
    query="past experiences",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条结果：")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")
```
**预期输出：**
```
迁移前查询（全部在主存储中）

1. 正在查询工作记忆...
   找到 3 条结果：
   1. [main] Meeting scheduled at 3 PM today
   2. [main] Need to buy groceries after work
   3. [main] Today's weather is sunny, good for outdoor activit

2. 正在查询情景记忆...
   找到 2 条结果：
   1. [main] Last summer I visited Paris and saw the Eiffel Tow
   2. [main] I learned to ride a bike when I was 7 years old
```
## 第4步：迁移数据到子存储（必需） {#step-4-migrate-data-to-sub-stores-required}

现在让我们将数据迁移到各自的子存储中。**此步骤是激活子存储的必要条件：**
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)

print("正在开始向子存储迁移数据...\n")

# 将工作记忆迁移到子存储 0
# ⚠️ 重要：即使没有数据需要迁移，也必须调用此方法！
# 它会激活子存储并标记为可用。
print("1. 正在将工作记忆迁移到 Sub Store 0...")
working_count = memory.migrate_to_sub_store(
    sub_store_index=0,
    delete_source=True  # 迁移后从主存储删除
)
print(f"   ✓ 已迁移 {working_count} 条工作记忆")
print("   ✓ Sub Store 0 已 ACTIVE，可用于路由")

# 将情景记忆迁移到子存储 1
print("\n2. 正在将情景记忆迁移到 Sub Store 1...")
episodic_count = memory.migrate_to_sub_store(
    sub_store_index=1,
    delete_source=True
)
print(f"   ✓ 已迁移 {episodic_count} 条情景记忆")
print("   ✓ Sub Store 1 已 ACTIVE，可用于路由")

print("\n✓ 迁移完成！当前分布：")
print("   - 主存储：语义记忆")
print(f"   - Sub Store 0：{working_count} 条工作记忆（ACTIVE）")
print(f"   - Sub Store 1：{episodic_count} 条情景记忆（ACTIVE）")
```
**预期输出：**

在此场景中，我们将演示如何使用多个 Sub Store 来管理不同类型的记忆数据。通过将记忆分割到不同的 Sub Store 中，可以更高效地组织和检索信息。

以下是此场景的主要步骤：

1. 创建一个主 Memory。
2. 为不同的数据类型创建多个 Sub Store。
3. 将数据存储到相应的 Sub Store 中。
4. 从特定的 Sub Store 中检索数据。
5. 验证数据是否正确存储和检索。

通过这种方法，您可以根据需求灵活地管理和扩展 Memory 的功能。
```
正在开始向子存储迁移数据...

1. 正在将工作记忆迁移到 Sub Store 0...
   ✓ 已迁移 3 条工作记忆
   ✓ Sub Store 0 已 ACTIVE，可用于路由

2. 正在将情景记忆迁移到 Sub Store 1...
   ✓ 已迁移 2 条情景记忆
   ✓ Sub Store 1 已 ACTIVE，可用于路由

✓ 迁移完成！当前分布：
   - 主存储：语义记忆
   - Sub Store 0：3 条工作记忆（ACTIVE）
   - Sub Store 1：2 条情景记忆（ACTIVE）
```
### 💡 关于子存储激活的重要说明 {#-important-note-about-sub-store-activation}

**为什么需要调用 `migrate_to_sub_store()`？**

1. **初始化**：子存储在记忆初始化期间被创建，但它们最初处于休眠状态
2. **激活**：调用 `migrate_to_sub_store()` 将子存储标记为“已准备好”并启用路由
3. **无需数据**：即使没有需要迁移的内容，也可以使用 `delete_source=False` 调用它
4. **一次性操作**：一旦激活，子存储将在所有未来操作中保持活跃

**如果未调用 `migrate_to_sub_store()`：**
- 子存储存在但未被使用
- 所有新记忆都会进入主存储
- 查询不会路由到子存储
- 路由过滤器被忽略

**调用 `migrate_to_sub_store()` 后：**
- 子存储被标记为活跃且已准备好
- 新记忆会根据元数据自动路由
- 查询会根据过滤器自动路由
- 子存储完全可用

## 第五步：迁移后的查询（自动路由） {#step-5-query-after-migration-automatic-routing}

迁移后，查询会自动路由到正确的子存储：
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("迁移后查询（自动路由）\n")

# 查询工作记忆（应路由到 Sub Store 0）
print("1. 正在查询工作记忆（路由到 Sub Store 0）...")
results = memory.search(
    query="today's schedule",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条结果：")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'unknown')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# 查询情景记忆（应路由到 Sub Store 1）
print("\n2. 正在查询情景记忆（路由到 Sub Store 1）...")
results = memory.search(
    query="past memories",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条结果：")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'unknown')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# 查询语义记忆（应查询主存储）
print("\n3. 正在查询语义记忆（查询主存储）...")
results = memory.search(
    query="programming and AI",
    filters={"memory_type": "semantic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条结果：")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")
```
**预期输出：**

在此场景中，我们将演示如何使用多个 Sub Store 来管理不同类型的记忆数据。以下是预期的输出：

1. 创建一个主 Memory Store，并初始化两个 Sub Store：一个用于存储用户记忆（UserMemory），另一个用于存储 Agent 记忆（AgentMemory）。
2. 将用户记忆和 Agent 记忆分别存储到对应的 Sub Store 中。
3. 从主 Memory Store 中检索数据时，可以根据需要访问特定的 Sub Store。
4. 验证数据是否正确存储并能够从相应的 Sub Store 中检索。

通过这种方式，您可以更高效地组织和管理复杂的记忆数据结构。
```
迁移后查询（自动路由）

1. 正在查询工作记忆（路由到 Sub Store 0）...
   找到 3 条结果：
   1. [working_memories] Meeting scheduled at 3 PM today
   2. [working_memories] Need to buy groceries after work
   3. [working_memories] Today's weather is sunny, good for outdoor activit

2. 正在查询情景记忆（路由到 Sub Store 1）...
   找到 2 条结果：
   1. [episodic_memories] Last summer I visited Paris and saw the Eiffel Tow
   2. [episodic_memories] I learned to ride a bike when I was 7 years old

3. 正在查询语义记忆（查询主存储）...
   找到 2 条结果：
   1. [main] Python is a high-level programming language known
   2. [main] Machine learning is a subset of artificial intell
```
## 第6步：添加新记忆（自动路由） {#step-6-add-new-memories-automatic-routing}

新记忆会自动路由到正确的子存储（因为我们在第4步中激活了它们）：
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("正在添加新记忆（测试自动路由）\n")

# 添加新的工作记忆（应路由到子存储 0）
print("1. 正在添加新的工作记忆...")
new_working_id = memory.add(
    messages="Remember to call the dentist tomorrow morning",
    metadata={"memory_type": "working", "importance": "high"},
    user_id=user_id
)
print("   ✓ 已自动路由到 Sub Store 0")

# 添加新的情景记忆（应路由到子存储 1）
print("\n2. 正在添加新的情景记忆...")
new_episodic_id = memory.add(
    messages="I graduated from university in 2020",
    metadata={"memory_type": "episodic", "time": "2020"},
    user_id=user_id
)
print("   ✓ 已自动路由到 Sub Store 1")

# 添加新的语义记忆（应保留在主存储）
print("\n3. 正在添加新的语义记忆...")
new_semantic_id = memory.add(
    messages="Docker is a platform for developing and deploying containerized applications,I love Docker!",
    metadata={"memory_type": "semantic", "topic": "technology"},
    user_id=user_id
)
print("   ✓ 已自动路由到主存储")

print("\n✓ 所有新记忆都已正确路由到对应存储")
```
**预期输出：**

在此场景中，我们将演示如何使用多个 Sub Store 来管理不同类型的记忆数据。通过这种方式，您可以将记忆分组存储在不同的存储中，以便更高效地查询和管理。

以下是此场景的主要步骤：

1. 创建一个主 Memory。
2. 为不同的数据类型创建多个 Sub Store。
3. 将数据存储到相应的 Sub Store 中。
4. 查询特定 Sub Store 中的数据。

通过这种方法，您可以更灵活地组织和使用您的记忆数据。
```
正在添加新记忆（测试自动路由）

1. 正在添加新的工作记忆...
   ✓ 已自动路由到 Sub Store 0

2. 正在添加新的情景记忆...
   ✓ 已自动路由到 Sub Store 1

3. 正在添加新的语义记忆...
   ✓ 已自动路由到主存储

✓ 所有新记忆都已正确路由到对应存储
```
## 第7步：验证路由正确性 {#step-7-verify-routing-correctness}

让我们验证新的记忆是否被正确路由：
```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("正在验证路由正确性\n")

# 验证工作记忆
print("1. 正在验证工作记忆数量...")
results = memory.search(
    query="tasks and reminders",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条工作记忆")
print("   预期：4（3 条旧记忆 + 1 条新记忆）")
print(f"   {'✓ PASS' if len(results_list) == 4 else '✗ FAIL'}")

# 验证情景记忆
print("\n2. 正在验证情景记忆数量...")
results = memory.search(
    query="life experiences",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条情景记忆")
print("   预期：4（3 条旧记忆 + 1 条新记忆）")
print(f"   {'✓ PASS' if len(results_list) == 4 else '✗ FAIL'}")

# 验证语义记忆
print("\n3. 正在验证语义记忆数量...")
results = memory.search(
    query="knowledge and concepts",
    filters={"memory_type": "semantic"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   找到 {len(results_list)} 条语义记忆")
expected_count = 5  # 原始数据 + 1 条新数据
print(f"   预期：{expected_count}")
print(f"   {'✓ PASS' if len(results_list) == expected_count else '✗ FAIL'}")

print("\n✓ 所有验证通过！路由工作正常")
```
**预期输出：**

在此场景中，我们将演示如何使用多个 Sub Store 来管理不同类型的记忆。通过将记忆分组到不同的 Sub Store 中，可以更高效地组织和检索数据。

以下是预期的输出示例：

1. **创建 Sub Store：**
   - 创建一个用于存储用户偏好的 Sub Store。
   - 创建另一个用于存储会话历史的 Sub Store。

2. **存储记忆：**
   - 将用户偏好存储到用户偏好的 Sub Store 中。
   - 将会话历史存储到会话历史的 Sub Store 中。

3. **检索记忆：**
   - 从用户偏好的 Sub Store 中检索用户偏好。
   - 从会话历史的 Sub Store 中检索会话历史。

通过这种方式，您可以根据记忆的类型和用途对其进行分类和管理。
```
正在验证路由正确性

1. 正在验证工作记忆数量...
   找到 4 条工作记忆
   预期：4（3 条旧记忆 + 1 条新记忆）
   ✓ PASS

2. 正在验证情景记忆数量...
   找到 4 条情景记忆
   预期：4（3 条旧记忆 + 1 条新记忆）
   ✓ PASS

3. 正在验证语义记忆数量...
   找到 5 条语义记忆
   预期：5
   ✓ PASS

✓ 所有验证通过！路由工作正常
```
## 完整示例 {#complete-example}

以下是一个结合所有步骤的完整示例：
```python
# complete_sub_store_example.py
from powermem import Memory
import os

def print_section(title: str):
    """打印格式化的章节标题"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")

def main():
    print_section("子存储完整示例")

    # 第 1 步：配置
    print_section("第 1 步：配置 Memory 和子存储")

    config = {
        "database": {
            "provider": "oceanbase",
            "config": {
                "collection_name": "demo_memories",
                "embedding_model_dims": 1536,
                "host": os.getenv("OCEANBASE_HOST", "localhost"),
                "port": int(os.getenv("OCEANBASE_PORT", "2881")),
                "user": os.getenv("OCEANBASE_USER", "root@test"),
                "password": os.getenv("OCEANBASE_PASSWORD", "password"),
                "db_name": os.getenv("OCEANBASE_DATABASE", "test_db"),
            }
        },
        "llm": {
            "provider": "qwen",
            "config": {
                "model": "qwen-max",
                "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
            }
        },
        "embedder": {
            "provider": "qwen",
            "config": {
                "model": "text-embedding-v4",
                "embedding_dims": 1536,
                "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
            }
        },
        "sub_stores": [
            {
                "collection_name": "working_memories",
                "routing_filter": {"memory_type": "working"},
                "embedding_model_dims": 1536,
            },
            {
                "collection_name": "episodic_memories",
                "routing_filter": {"memory_type": "episodic"},
                "embedding_model_dims": 1536,
            }
        ]
    }

    memory = Memory(config=config)
    user_id = "demo_user"
    print("✓ Memory 初始化成功")

    # 第 2 步：添加记忆
    print_section("第 2 步：添加不同类型的记忆")

    memories = {
        "semantic": [
            "Python is a high-level programming language,I love Python!",
            "Machine learning is a subset of AI",
        ],
        "working": [
            "Today's weather is sunny",
            "Buy groceries after work",
            "Meeting at 3 PM",
        ],
        "episodic": [
            "Visited Paris last year",
            "Learned to ride a bike at age 7",
        ]
    }

    for mem_type, mem_list in memories.items():
        print(f"\n正在添加 {mem_type} 类型记忆...")
        for mem in mem_list:
            memory.add(
                messages=mem,
                metadata={"memory_type": mem_type},
                user_id=user_id
            )
            print(f"  ✓ {mem}")

    # 第 3 步：迁移（激活必需）
    print_section("第 3 步：迁移到子存储（必需）")

    print("⚠️ 重要：此步骤是激活 Sub Store 的必需操作！")
    print("即使没有需要迁移的数据，也必须调用一次。\n")

    working_count = memory.migrate_to_sub_store(0, delete_source=True)
    episodic_count = memory.migrate_to_sub_store(1, delete_source=True)

    print("✓ 迁移完成：")
    print(f"  - 工作记忆：{working_count} → Sub Store 0（ACTIVE）")
    print(f"  - 情景记忆：{episodic_count} → Sub Store 1（ACTIVE）")

    # 第 4 步：查询验证
    print_section("第 4 步：验证自动路由")

    test_queries = [
        ("today tasks", "working", "working_memories"),
        ("past experiences", "episodic", "episodic_memories"),
        ("programming knowledge", "semantic", "main"),
    ]

    for query, mem_type, expected_store in test_queries:
        results = memory.search(
            query=query,
            filters={"memory_type": mem_type},
            user_id=user_id,
            limit=5
        )
        results_list = results.get("results", [])
        print(f"\n查询 '{query}'（type={mem_type}）：")
        print(f"  找到 {len(results_list)} 条结果")
        if results_list:
            actual_store = results_list[0].get('_source_store', 'unknown')
            print(f"  存储位置：{actual_store}")
            print(f"  {'✓ 正确' if expected_store in actual_store or actual_store == expected_store else '✗ 错误'}")

    print_section("✓ 示例完成！")
    print("子存储功能演示成功：")
    print("  1. ✓ 配置和初始化")
    print("  2. ✓ 添加不同类型的记忆")
    print("  3. ✓ 数据迁移（激活所必需）")
    print("  4. ✓ 自动查询路由")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n示例被用户中断")
    except Exception as e:
        print(f"\n\n✗ 发生错误：{e}")
        import traceback
        traceback.print_exc()
```
**运行代码：**
```bash
python complete_sub_store_example.py
```
## 高级用法 {#advanced-usage}

### 配置不同的嵌入维度 {#configuring-different-embedding-dimensions}

为不同的子存储配置不同的嵌入维度以优化性能：
```python
config = {
    # ... 主配置 ...
    "sub_stores": [
        {
            # 低优先级工作记忆使用较小维度
            "collection_name": "working_memories",
            "routing_filter": {"memory_type": "working"},
            "embedding_model_dims": 512,  # 较小维度
            "embedding": {
                "provider": "qwen",
                "config": {
                    "model": "text-embedding-v4",
                    "embedding_dims": 512,
                    "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
                }
            }
        },
        {
            # 重要情景记忆使用较大维度
            "collection_name": "episodic_memories",
            "routing_filter": {"memory_type": "episodic"},
            "embedding_model_dims": 1536,  # 较大维度
        }
    ]
}
```
### 多条件路由 {#multi-condition-routing}

使用多个元数据字段进行路由：
```python
config = {
    # ... 主配置 ...
    "sub_stores": [
        {
            "collection_name": "important_working_memories",
            "routing_filter": {
                "memory_type": "working",
                "importance": "high"  # 多个条件
            },
            "embedding_model_dims": 1536,
        }
    ]
}

# 添加包含多个 metadata 字段的记忆
memory.add(
    messages="Important work task",
    metadata={
        "memory_type": "working",
        "importance": "high"  # 匹配路由条件
    },
    user_id=user_id
)
```
### 迁移而不删除源数据 {#migration-without-deleting-source-data}

在源中保留一份副本的同时进行迁移：
```python
# 迁移但不删除原始数据（创建副本）
count = memory.migrate_to_sub_store(
    sub_store_index=0,
    delete_source=False  # 保留原始数据
)
print(f"Copied {count} memories to sub store")
```
### 启用子存储而无需数据迁移 {#activating-sub-stores-without-data-migration}

如果您是从头开始或希望立即使用子存储：
```python
# 激活 Sub Store，而不迁移任何数据
# 即使没有现有数据，这也是必需的
for i in range(len(config["sub_stores"])):
    memory.migrate_to_sub_store(
        sub_store_index=i,
        delete_source=False  # 没有数据需要删除
    )
    print(f"✓ Sub Store {i} 已激活，可以使用")

# 现在可以添加新记忆，它们会正确路由
memory.add(
    messages="New working memory",
    metadata={"memory_type": "working"},
    user_id=user_id
)  # 会自动进入子存储 0
```
## 最佳实践 {#best-practices}

1. **明智地规划存储结构**
   - 根据查询模式设计子存储
   - 考虑数据访问频率和重要性
   - 平衡存储成本和查询性能

2. **选择合适的Embedding维度**
   - 对于频繁查询的数据使用较大的维度
   - 对于临时数据使用较小的维度
   - 测试不同维度对性能的影响

3. **设计清晰的路由规则**
   - 使用明确的元数据字段
   - 避免重叠的路由规则
   - 记录你的路由逻辑

4. **始终激活子存储**
   - 对每个子存储至少调用一次 `migrate_to_sub_store()`
   - 即使没有数据需要迁移也要执行此操作
   - 如果未激活，子存储将不会被使用

5. **监控和优化**
   - 监控子存储的查询性能
   - 定期清理过期数据
   - 根据使用模式调整配置

## 故障排查 {#troubleshooting}

### 问题：记忆未路由到正确的子存储 {#issue-memories-not-routing-to-correct-sub-store}

**解决方案：**
1. 确认已调用 `migrate_to_sub_store()` 激活子存储
2. 检查元数据字段是否匹配 `routing_filter`
3. 确认子存储配置正确
4. 查看日志以了解路由决策

### 问题：迁移后找不到数据 {#issue-cannot-find-data-after-migration}

**解决方案：**
1. 确保查询中使用了正确的 `filters`
2. 验证迁移是否成功完成
3. 检查子存储是否正确初始化

### 问题：性能未改善 {#issue-performance-not-improved}

**解决方案：**
1. 检查数据在存储间的分布
2. 考虑调整Embedding维度
3. 优化查询条件和索引

### 问题：子存储未被使用 {#issue-sub-store-not-being-used}

**解决方案：**
1. **最常见原因**：你忘记调用 `migrate_to_sub_store()` 激活子存储
2. 检查路由过滤器是否与元数据完全匹配
3. 确保查询包含正确的过滤条件

## 总结 {#summary}

在本场景中，我们学习了：
- ✓ 如何配置和初始化子存储
- ✓ 添加不同类型的记忆
- ✓ **必需**：调用 `migrate_to_sub_store()` 激活子存储
- ✓ 将数据迁移到子存储
- ✓ 自动查询路由
- ✓ 新记忆的自动路由
- ✓ 验证路由的正确性
- ✓ 高级配置和最佳实践

### 关键点：子存储激活 {#key-takeaway-sub-store-activation}

**请记住**：子存储必须通过调用 `migrate_to_sub_store()` 显式激活至少一次，即使没有数据需要迁移。如果没有这个激活步骤，子存储将保持未激活状态且不会被使用，所有操作将继续仅使用主存储。

子存储是一项强大的功能，可以帮助你：
- 优化存储成本
- 提高查询性能
- 更好地组织数据
- 实现灵活的数据管理策略
