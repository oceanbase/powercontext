---
title: 快速入门
sidebar_label: 快速入门
---

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem (`pip install powermem`)

## 配置 {#configuration}

在使用 powermem 之前，需要对其进行配置。Powermem 可以自动从项目目录中的 `.env` 文件加载配置。这是为您的使用场景配置 powermem 的推荐方式。

### 为什么使用 .env 文件？ {#why-use-a-env-file}

使用 `.env` 文件可以让您：
- 将配置与代码分离
- 轻松切换不同环境（开发、预发布、生产）
- 保护敏感凭据（如 API 密钥、数据库密码）
- 分享配置模板而不暴露机密信息

### 创建 .env 文件 {#creating-a-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并配置您的设置：
   - **LLM Provider**: 选择您的语言模型提供商（Qwen、OpenAI、Anthropic 等）
   - **Embedding Provider**: 选择文本如何转换为向量
   - **Vector Store**: 选择您的数据库（开发环境使用 SQLite，生产环境使用 OceanBase）

> **注意：** 当您调用 `auto_config()` 时，powermem 会自动：
> - 在当前目录中查找 `.env` 文件
> - 从环境变量中加载配置
> - 如果未找到配置，则使用合理的默认值

有关更多配置选项，请参阅 `.env.example` 中的完整示例或参考 [配置指南](./0003-configuration.md)。

**替代方法：** 您可以使用 PowerMem CLI 以交互方式创建或更新您的 `.env`：运行 `pmem config init`。详情请参阅 [CLI 使用指南](./0012-cli_usage.md)。

## 初始化记忆 {#initializing-memory}

使用 powermem 的第一步是创建一个记忆实例。此实例将处理您的应用程序的所有记忆操作。

### 理解 Memory 和 auto_config() {#understanding-memory-and-auto_config}

`Memory` 类是核心的记忆管理类。要初始化它：
- 使用 `auto_config()` 从您的 `.env` 文件中自动加载配置
- 将配置传递给 `Memory`，以创建具有适当设置的实例
- `Memory` 类负责初始化向量存储和嵌入

让我们创建一个简单的 Python 脚本并初始化 powermem：
```python
from powermem import Memory, auto_config

# 加载配置（自动从 .env 加载或使用默认值）
config = auto_config()

# 创建 Memory 实例
memory = Memory(config=config)

print("✓ Memory initialized successfully!")
```
### 使用 JSON/字典配置 {#using-jsondictionary-configuration}

除了使用 `.env` 文件，你还可以直接以 Python 字典（类似 JSON 的格式）传递配置。这在以下情况下非常有用：
- 你想从 JSON 文件加载配置
- 你需要以编程方式生成配置
- 你将配置嵌入到应用程序代码中

以下是使用字典配置的示例：
```python
from powermem import Memory

# 将配置定义为字典（类似 JSON 的格式）
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'temperature': 0.7
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
                'host': 'localhost',
                'port': 2881,
                'user': 'root@sys',
                'password': 'password',
                'db_name': 'powermem'
            },
            'embedding_model_dims': 1536,
            'index_type': 'IVF_FLAT',
            'vidx_metric_type': 'cosine'
        }
    }
}

# 使用字典配置创建 Memory 实例
memory = Memory(config=config)

print("✓ Memory initialized with JSON config!")
```
## 添加您的第一个记忆 {#add-your-first-memory}

现在您已经初始化了 PowerMem，让我们添加您的第一个记忆。添加记忆可以存储信息，之后可以通过语义搜索检索这些信息。

### 理解 add() 方法 {#understanding-the-add-method}

`add()` 方法的功能：
- 接收描述记忆的文本消息
- 将其与一个 `user_id` 关联，以确保每个用户的记忆是隔离的
- 将文本转换为向量嵌入，用于语义搜索
- 将其存储在您配置的向量数据库中
- 返回包含记忆 ID 和其他元数据的结果

### 重要参数 {#important-parameters}

- **`messages`**: 您想要存储为记忆的文本内容
- **`user_id`**: 用户的唯一标识符（多用户隔离所必需）
- **`infer`**: 是否使用智能去重（默认值：`True`）

让我们添加一个简单的记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 添加一条记忆
result = memory.add(
    messages="User likes Python programming",
    user_id="user123"
)

# 从结果中获取 memory ID
results_list = result.get('results', [])
memory_id = results_list[0].get('id', 'N/A') if results_list else 'N/A'
print(f"✓ Memory added! ID: {memory_id}")
```
## 添加多个记忆 {#add-multiple-memories}

在实际应用中，您通常需要为用户添加多个记忆。这在以下情况下非常有用：
- 导入历史数据
- 处理批量对话
- 使用已知信息初始化用户档案

### 添加多个记忆的最佳实践 {#best-practices-for-adding-multiple-memories}

- **使用一致的 user_id**：对于同一用户，始终使用相同的 `user_id`
- **顺序处理**：对于小批量数据，顺序处理是可以的
- **考虑批量操作**：对于大型数据集，考虑使用异步操作（参见 [异步指南](./0002-getting_started_async.md)）
- **处理错误**：在生产代码中，将操作包装在 try-except 块中

让我们为一个用户添加多个记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加多条记忆
memories = [
    "User likes Python programming",
    "User prefers email support over phone calls",
    "User works as a software engineer",
    "User favorite color is blue"
]

for mem in memories:
    result = memory.add(messages=mem, user_id=user_id)
    print(f"✓ Added: {mem}")

print(f"\n✓ All memories added for user {user_id}")
```
## 搜索记忆 {#search-memories}

PowerMem 最强大的功能之一是语义搜索。与传统的关键词搜索不同，语义搜索基于意义和上下文查找记忆，而不仅仅是精确的词匹配。

### 语义搜索的工作原理 {#how-semantic-search-works}

1. **查询嵌入**：您的搜索查询会被转换为一个向量嵌入
2. **相似度计算**：PowerMem 将您的查询向量与所有存储的记忆向量进行比较
3. **排序**：结果根据语义相似度（余弦相似度）进行排序
4. **过滤**：仅考虑指定 `user_id` 的记忆

### 理解搜索参数 {#understanding-search-parameters}

- **`query`**：您要搜索的文本（可以是自然语言）
- **`user_id`**：将搜索限制为特定用户的记忆
- **`limit`**：返回结果的最大数量（默认值：10）
- **`filters`**：可选的元数据过滤器（详见下一节）

### 为什么语义搜索如此强大 {#why-semantic-search-is-powerful}

语义搜索使您能够找到相关记忆，即使：
- 查询使用的词与存储的记忆不同
- 查询的措辞不同
- 您正在寻找概念上相似的信息

让我们来搜索记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 先添加一些记忆
memory.add("User likes Python programming", user_id=user_id)
memory.add("User prefers email support", user_id=user_id)
memory.add("User works as a software engineer", user_id=user_id)

# 搜索记忆
print("Searching for 'user preferences'...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    limit=5
)

print(f"\nFound {len(results.get('results', []))} memories:")
for i, result in enumerate(results.get('results', []), 1):
    print(f"  {i}. {result['memory']}")
```
## 添加元数据 {#add-metadata}

元数据是可以附加到记忆上的附加信息，用于帮助组织、筛选和分类记忆。这在生产应用中尤其有用，例如当您需要：

- **分类记忆**：将相关记忆分组在一起
- **追踪来源**：了解记忆的来源（对话、表单、API 等）
- **设置重要性级别**：为某些记忆设定优先级
- **添加时间戳**：记录记忆的创建或更新时间
- **存储自定义属性**：存储与您的使用场景相关的任何附加信息

### 使用元数据的好处 {#benefits-of-using-metadata}

- **更好的组织**：以逻辑方式结构化您的记忆
- **高效筛选**：快速找到符合特定条件的记忆
- **丰富的上下文**：存储附加信息而不会使记忆文本变得杂乱
- **分析能力**：追踪模式和使用统计数据

### 元数据最佳实践 {#metadata-best-practices}

- **使用一致的键**：为您的元数据键定义一个模式
- **保持简单**：不要让元数据结构过于复杂
- **使用有意义的值**：让元数据值可搜索且有意义
- **考虑索引**：某些向量存储支持对元数据字段进行索引

让我们通过添加带有元数据的记忆来实现更好的组织：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加带元数据的记忆
memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    metadata={
        "category": "preference",
        "importance": "high",
        "source": "conversation"
    }
)

memory.add(
    messages="User prefers email support",
    user_id=user_id,
    metadata={
        "category": "communication",
        "importance": "medium"
    }
)

print("✓ Memories added with metadata")
```
> **提示：** 元数据与记忆一起存储，并可随搜索结果一起检索。您还可以使用元数据来过滤搜索，如下一节所示。

## 使用元数据过滤进行搜索 {#search-with-metadata-filters}

元数据过滤允许您根据元数据值缩小搜索结果范围。当您拥有大量记忆并需要找到特定子集时，这非常有用。

### 元数据过滤的工作原理 {#how-metadata-filtering-works}

当您向 `search()` 提供一个 `filters` 参数时：
1. PowerMem 首先执行语义搜索以找到相关记忆
2. 然后过滤结果，仅包含符合您元数据条件的记忆
3. 结果仍然按语义相似性排序

### 过滤语法 {#filter-syntax}

过滤使用一个字典，其中：
- **键** 是元数据字段名称
- **值** 是要匹配的确切值

### 元数据过滤的使用场景 {#use-cases-for-metadata-filters}

- **类别过滤**：查找所有“偏好”记忆
- **日期范围**：查找特定时间段的记忆
- **来源过滤**：查找来自特定来源的记忆
- **重要性过滤**：仅查找高重要性的记忆
- **组合过滤**：同时使用多个过滤条件

让我们使用元数据过滤来搜索记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加带元数据的记忆
memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    metadata={"category": "preference"}
)

memory.add(
    messages="User prefers email support",
    user_id=user_id,
    metadata={"category": "communication"}
)

# 使用元数据过滤搜索
# 注意：category 会从 metadata 中提取并作为顶层字段存储
print("Searching with metadata filter...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    filters={"category": "preference"}
)

print(f"\nFound {len(results.get('results', []))} memories:")
for result in results.get('results', []):
    print(f"  - {result['memory']}")
    print(f"    Metadata: {result.get('metadata', {})}")
```
> **注意：** 元数据过滤器与语义搜索结合使用。结果将既与您的查询在语义上相关，又符合您的元数据条件。这使您可以精确控制返回的记忆内容。

## 获取所有记忆 {#get-all-memories}

有时您需要在不执行搜索的情况下检索用户的所有记忆。`get_all()` 方法会返回与特定 `user_id` 关联的所有记忆。

### 何时使用 get_all() {#when-to-use-get_all}

- **用户资料展示**：显示关于用户的所有存储信息
- **数据导出**：导出所有记忆以进行备份或迁移
- **调试**：检查所有记忆以了解存储的内容
- **分析**：分析所有记忆以发现模式或统计数据

### 重要注意事项 {#important-considerations}

- **性能**：对于拥有大量记忆的用户，`get_all()` 可能会较慢
- **内存使用**：较大的结果集会消耗更多内存
- **分页**：在生产环境中考虑实现分页
- **尽量使用搜索**：如果您需要特定的记忆，请使用 `search()` 方法

### 返回格式 {#return-format}

`get_all()` 方法返回的格式与 `search()` 相同，包含一个 `results` 列表，其中包含用户的所有记忆。

让我们检索用户的所有记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 先添加一些记忆
memory.add("User likes Python", user_id=user_id)
memory.add("User prefers email", user_id=user_id)
memory.add("User works as engineer", user_id=user_id)

# 获取所有记忆
all_memories = memory.get_all(user_id=user_id)

print(f"\nTotal memories for {user_id}: {len(all_memories.get('results', []))}")
print("\nAll memories:")
for i, mem in enumerate(all_memories.get('results', []), 1):
    print(f"  {i}. {mem['memory']}")
```
> **提示：** 对于每个用户有许多记忆的生产应用程序，建议使用带有广泛查询的 `search()` 方法，或实现分页功能，以避免一次性加载所有记忆。

## 更新记忆 {#update-a-memory}

随着时间的推移，当信息发生变化或需要更详细时，您可能需要更新现有的记忆。`update()` 方法允许您修改存储记忆的内容。

### 何时更新记忆 {#when-to-update-memories}

- **信息变化**：用户偏好或详细信息发生了变化
- **添加细节**：为记忆扩展更多信息
- **修正错误**：修复错误或过时的信息
- **优化**：使记忆更加具体或准确

### 理解记忆更新 {#understanding-memory-updates}

当您更新记忆时：
1. 记忆内容将被新内容替换
2. 为更新后的内容生成一个新的向量嵌入
3. 记忆 ID 保持不变（便于跟踪）
4. 元数据也可以选择性地更新

### 获取记忆 ID {#getting-the-memory-id}

要更新记忆，您需要其 ID。您可以通过以下方式获取 ID：
- 创建记忆时 `add()` 的返回结果
- `search()` 或 `get_all()` 操作的返回结果
- 将 ID 存储在您的应用程序数据库中

让我们更新一个现有的记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一条记忆（使用 infer=False 以获得可预测行为）
result = memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    infer=False  # 禁用智能模式以获得可预测行为
)

# 从结果中获取 memory ID
results_list = result.get('results', [])
if not results_list:
    raise ValueError("No memory was added. Check the result: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("Memory ID not found in result")

# 更新记忆
updated = memory.update(
    memory_id=memory_id,
    content="User loves Python programming, especially for data science"
)

print(f"✓ Memory updated!")
print(f"  Old: User likes Python programming")
print(f"  New: {updated.get('data', 'N/A')}")
```
> **注意：** 更新后，记忆的向量嵌入会被重新生成，因此可以通过新内容进行语义搜索。旧内容将被完全替换。

## 删除记忆 {#delete-a-memory}

有时您需要移除不再相关或不准确的记忆。`delete()` 方法会永久从存储中移除一条记忆。

### 何时删除记忆 {#when-to-delete-memories}

- **过时信息**：信息已不再真实
- **用户请求**：用户明确要求移除某条记忆
- **隐私合规**：为满足 GDPR 或其他隐私要求而移除数据
- **数据清理**：移除测试或临时记忆

### 理解删除操作 {#understanding-deletion}

- **永久性**：删除是永久性的，无法撤销
- **需要记忆 ID**：您必须知道记忆的 ID 才能删除
- **返回布尔值**：方法成功时返回 `True`，失败时返回 `False`
- **无级联影响**：删除一条记忆不会影响其他记忆

### 最佳实践 {#best-practices}

- **删除前确认**：在生产环境中，建议在删除前请求确认
- **记录删除操作**：保留删除内容及时间的审计日志
- **处理错误**：检查返回值并适当处理失败情况
- **备份重要数据**：在批量删除前考虑备份数据

让我们删除一条记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一条记忆（使用 infer=False 以获得可预测行为）
result = memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    infer=False  # 禁用智能模式以获得可预测行为
)

# 从结果中获取 memory ID
results_list = result.get('results', [])
if not results_list:
    raise ValueError("No memory was added. Check the result: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("Memory ID not found in result")

# 删除记忆
success = memory.delete(memory_id)

if success:
    print(f"✓ Memory {memory_id} deleted successfully!")
else:
    print(f"✗ Failed to delete memory")
```
> **警告：** 删除是永久性的。在调用 `delete()` 之前，请确保您确实想要删除该记忆。如果需要恢复已删除的记忆，建议在生产环境中实现软删除机制。

## 删除所有记忆 {#delete-all-memories}

`delete_all()` 方法会删除特定用户的所有记忆。这在以下情况下非常有用：
- **账户删除**：当用户删除其账户时移除所有数据
- **数据重置**：清除所有记忆以进行测试或重置用户状态
- **隐私合规**：根据隐私法规完全删除数据

### 重要注意事项 {#important-considerations-1}

- **不可逆**：用户的所有记忆将被永久删除
- **特定用户**：仅影响指定 `user_id` 的记忆
- **无确认**：该方法会立即执行，无需确认
- **返回布尔值**：成功时返回 `True`，失败时返回 `False`

### 何时使用 delete_all() {#when-to-use-delete_all}

- **用户账户删除**：当用户请求完全删除数据时
- **测试**：在测试运行之间清除测试数据
- **数据迁移**：在导入新数据之前删除旧数据
- **隐私合规**：满足 GDPR 中“被遗忘权”的要求

### 生产环境最佳实践 {#production-best-practices}

- **要求确认**：始终要求用户明确确认
- **记录操作**：记录谁在何时删除了什么
- **先备份**：在批量删除之前考虑备份数据
- **验证用户身份**：确保请求者有权限进行删除操作

让我们为一个用户删除所有记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 先添加一些记忆
memory.add("Memory 1", user_id=user_id)
memory.add("Memory 2", user_id=user_id)
memory.add("Memory 3", user_id=user_id)

# 删除前获取数量
all_memories = memory.get_all(user_id=user_id)
count_before = len(all_memories.get('results', []))

# 删除所有记忆（返回 True/False）
success = memory.delete_all(user_id=user_id)

if success:
    print(f"✓ Deleted {count_before} memories for {user_id}")
else:
    print(f"✗ Failed to delete memories")
```
> **警告：** `delete_all()` 会永久删除指定用户的所有记忆。此操作无法撤销。在生产环境中使用时需极其谨慎。在允许执行此操作之前，请始终实施适当的授权检查和用户确认。

## 生态系统集成 {#ecosystem-integrations}

除了 Python SDK，PowerMem 提供了一些官方集成，这些集成为现有的 AI 客户端和 IDE 提供持久化记忆——所有这些都指向相同的后端（HTTP API 服务器或本地 `pmem` CLI），无需为每个客户端重写 schema。

- **[Claude Code](../integrations/claude_code.md)** — 通过 `memory-powermem` 插件将 Claude Code 连接到 PowerMem。默认的 **HTTP 模式** 通过钩子（`SessionEnd` / `PostCompact` 写入会话记录，`UserPromptSubmit` 每轮注入相关记忆）静默捕获每个会话；可选的 **MCP 模式** 添加了聊天内的 `search_memories` / `add_memory` 工具。在 Claude Code 机器上无需安装 Python。

查看 **[生态系统集成](../integrations/overview.md)** 获取完整列表，以及 **[集成指南](./0009-integrations.md)** 了解框架接入（LangChain、LangGraph、FastAPI、自定义提供商）。

## 下一步 {#next-steps}

- [配置指南](./0003-configuration.md) — 提供商、存储后端、环境变量
- [CLI 使用指南](./0012-cli_usage.md) — `pmem` 命令和交互式 shell
- [生态系统集成](../integrations/overview.md) — Claude Code 和其他 AI 客户端
