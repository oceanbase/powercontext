# Memory API 参考 {#memory-api-reference}

`Memory` 是 PowerMem 中核心的同步记忆管理类。它为 LLM 应用中的记忆存储、检索和管理提供了一个简单的接口。

## 类: Memory {#class-memory}
```python
from powermem import Memory

memory = Memory(config=config)
```
### 构造函数 {#constructor}

#### Memory(config=None, storage_type=None, llm_provider=None, embedding_provider=None, agent_id=None) {#memoryconfignone-storage_typenone-llm_providernone-embedding_providernone-agent_idnone}

创建一个新的 Memory 实例。

**参数:**
- `config` (dict, 可选): 配置字典。如果为 None，则从环境变量加载。
- `storage_type` (str, 可选): 存储后端类型（已弃用，请使用 config）。
- `llm_provider` (str, 可选): LLM 提供商名称（已弃用，请使用 config）。
- `embedding_provider` (str, 可选): Embedding 提供商名称（已弃用，请使用 config）。
- `agent_id` (str, 可选): 用于Multi-Agent 场景的 Agent 标识符。

**示例:**
```python
from powermem import Memory, auto_config

# 从 .env 自动加载
config = auto_config()
memory = Memory(config=config, agent_id="my_agent")
```
### 核心方法 {#core-methods}

#### add(messages, user_id=None, agent_id=None, run_id=None, metadata=None, filters=None, scope=None, memory_type=None, prompt=None, infer=True) {#addmessages-user_idnone-agent_idnone-run_idnone-metadatanone-filtersnone-scopenone-memory_typenone-promptnone-infertrue}

向存储中添加一条记忆。

**参数:**
- `messages` (str | dict | list[dict]): 记忆内容。可以是：
  - 一个字符串（将被转换为一条消息）
  - 一个包含 `role` 和 `content` 的单条消息字典
  - 一个 OpenAI 格式的消息字典列表
- `user_id` (str, 可选): 用户标识符。
- `agent_id` (str, 可选): Agent 标识符。
- `run_id` (str, 可选): 运行/会话标识符。
- `metadata` (dict, 可选): 附加元数据。
- `filters` (dict, 可选): 用于子存储路由和高级过滤的过滤元数据。根据元数据值将记忆路由到特定子存储。格式详情见下文的 [过滤参数格式](#filter-parameter-format)。
- `scope` (str, 可选): 记忆范围（例如，'user'、'agent'、'session'）。
- `memory_type` (str, 可选): 记忆类型分类。
- `prompt` (str, 可选): 用于智能处理的自定义 prompt。
- `infer` (bool): 启用智能记忆处理（默认值: True）。

**返回值:**
- `dict`: 包含已添加记忆信息的结果。

**示例:**
```python
# 简单文本记忆
result = memory.add(
    messages="User likes Python programming",
    user_id="user123"
)

# 使用智能处理（默认）
result = memory.add(
    messages=[
        {"role": "user", "content": "I'm Alice, a software engineer"},
        {"role": "assistant", "content": "Nice to meet you!"}
    ],
    user_id="user123"
    # 默认 infer=True，启用智能事实提取
)

# 单条消息字典
result = memory.add(
    messages={"role": "user", "content": "I prefer dark mode"},
    user_id="user123"
)
```
#### search(query, user_id=None, agent_id=None, run_id=None, filters=None, limit=30, threshold=None) {#searchquery-user_idnone-agent_idnone-run_idnone-filtersnone-limit30-thresholdnone}

使用语义相似性搜索记忆。

**参数:**
- `query` (str): 搜索查询字符串。
- `user_id` (str, optional): 按用户 ID 过滤。
- `agent_id` (str, optional): 按 Agent ID 过滤。
- `run_id` (str, optional): 按运行 ID 过滤。
- `filters` (dict, optional): 用于高级过滤的元数据过滤器。详见下文的[过滤器参数格式](#filter-parameter-format)文档。
- `limit` (int): 返回结果的最大数量（默认值: 30）。
- `threshold` (float, optional): 用于过滤结果的相似性阈值（0.0-1.0）。

**返回值:**
- `dict`: 包含记忆和分数的搜索结果。格式：
  ```python
  {
      "results": [
          {
              "memory": "memory content",
              "metadata": {...},
              "score": 0.85,
              "id": 123,
              ...
          }
      ],
      "relations": [...]  # 如果启用了图存储
  }
  ```
**示例:**
```python
results = memory.search(
    query="user preferences",
    user_id="user123",
    limit=5,
    threshold=0.7  # 只返回相似度 >= 0.7 的结果
)

for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
```
### Filter 参数格式 {#filter-parameter-format}

`filters` 参数允许您对记忆元数据进行高级过滤。它支持使用各种运算符的简单和复杂过滤格式。

#### 简单过滤格式 {#simple-filter-format}

**精确匹配：**
```python
# 按精确值过滤
filters = {"category": "food"}
filters = {"priority": "high"}
filters = {"status": "active"}
```
**列出值（IN 操作符）：**
```python
# 过滤字段值在列表中的记录
filters = {"category": ["food", "drink", "dessert"]}
filters = {"tag": ["important", "urgent"]}
```
**None/Null 检查：**
```python
# 过滤字段为 None 的记录
filters = {"deleted_at": None}
```
#### 比较运算符 {#comparison-operators}

使用比较运算符进行数字或日期比较：
```python
# 单个比较运算符
filters = {"rating": {"gte": 4.0}}  # rating >= 4.0
filters = {"price": {"lt": 100}}     # price < 100
filters = {"age": {"gt": 18}}        # age > 18
filters = {"score": {"lte": 0.8}}    # score <= 0.8

# 同一字段上的多个运算符（AND 逻辑）
filters = {"rating": {"gte": 4.0, "lte": 5.0}}  # 4.0 <= rating <= 5.0
filters = {"price": {"gt": 10, "lt": 100}}      # 10 < price < 100
```
**支持的比较运算符：**
- `eq`：等于（`==`）
- `ne`：不等于（`!=`）
- `gt`：大于（`>`）
- `gte`：大于或等于（`>=`）
- `lt`：小于（`<`）
- `lte`：小于或等于（`<=`）

#### 列表运算符 {#list-operators}

**IN 和 NOT IN：**
```python
# 字段值在列表中
filters = {"category": {"in": ["food", "drink"]}}
filters = {"user_id": {"in": ["user1", "user2", "user3"]}}

# 字段值不在列表中
filters = {"status": {"nin": ["deleted", "archived"]}}
filters = {"tag": {"nin": ["deprecated"]}}
```
#### 字符串模式匹配 {#string-pattern-matching}

**LIKE 和 ILIKE：**
```python
# 区分大小写的模式匹配（LIKE）
filters = {"name": {"like": "%python%"}}      # 包含 "python"
filters = {"email": {"like": "%@example.com"}} # 以 "@example.com" 结尾

# 不区分大小写的模式匹配（ILIKE）
filters = {"title": {"ilike": "%tutorial%"}}  # 包含 "tutorial"（不区分大小写）
filters = {"description": {"ilike": "how to%"}} # 以 "how to" 开头（不区分大小写）
```
**注意：** 使用 `%` 作为模式匹配的通配符。

#### 逻辑运算符（AND/OR） {#logical-operators-andor}

使用逻辑运算符组合多个条件：

**AND 逻辑：**
```python
# 所有条件都必须为真
filters = {
    "AND": [
        {"user_id": "alice"},
        {"category": "food"},
        {"rating": {"gte": 4.0}}
    ]
}
```
**或逻辑 (OR Logic):**
```python
# 至少一个条件为真
filters = {
    "OR": [
        {"rating": {"gte": 4.0}},
        {"priority": "high"}
    ]
}
```
**嵌套逻辑：**
```python
# 复杂嵌套条件
filters = {
    "AND": [
        {"user_id": "alice"},
        {
            "OR": [
                {"rating": {"gte": 4.0}},
                {"priority": "high"}
            ]
        },
        {"category": {"in": ["food", "drink"]}}
    ]
}
```
#### 可筛选字段 {#filterable-fields}

您可以根据以下字段进行筛选：

**标准字段：**
- `user_id` (str): 用户标识符
- `agent_id` (str): Agent 标识符
- `run_id` (str): 运行/会话标识符
- `actor_id` (str): Actor 标识符
- `hash` (str): 记忆哈希值
- `created_at` (str/datetime): 创建时间戳
- `updated_at` (str/datetime): 最后更新时间戳
- `category` (str): 记忆类别

**自定义元数据字段：**
在添加记忆时存储于 `metadata` 字典中的任何字段也可以进行筛选：
```python
# 添加带自定义 metadata 的记忆时
memory.add(
    messages="User likes Python",
    user_id="user123",
    metadata={
        "tags": ["programming", "python"],
        "priority": "high",
        "rating": 5.0,
        "department": "engineering"
    }
)

# 按自定义 metadata 字段过滤
filters = {"tags": {"in": ["programming"]}}
filters = {"priority": "high"}
filters = {"rating": {"gte": 4.0}}
filters = {"department": "engineering"}
```
#### 完整示例 {#complete-examples}

**示例 1: 按 User ID 和 Category 筛选**
```python
results = memory.search(
    query="favorite foods",
    filters={"category": "food"}
)
```
**示例 2：按评分范围筛选**
```python
results = memory.search(
    query="restaurant recommendations",
    filters={"rating": {"gte": 4.0, "lte": 5.0}}
)
```
**示例 3：通过标签筛选（IN 操作符）**
```python
results = memory.search(
    query="programming tips",
    filters={"tags": {"in": ["python", "tutorial"]}}
)
```
**示例 4：按时间范围筛选**
```python
from datetime import datetime, timedelta

# 最近 7 天创建的记忆
week_ago = (datetime.now() - timedelta(days=7)).isoformat()
filters = {"created_at": {"gte": week_ago}}

results = memory.search(
    query="recent conversations",
    filters=filters
)
```
**示例 5：带有 AND/OR 的复杂过滤器**
```python
results = memory.search(
    query="important tasks",
    filters={
        "AND": [
            {"priority": "high"},
            {
                "OR": [
                    {"status": "pending"},
                    {"status": "in_progress"}
                ]
            },
            {"category": {"in": ["work", "personal"]}}
        ]
    }
)
```
**示例 6: 按自定义元数据筛选**
```python
# 搜索包含特定自定义标签的记忆
results = memory.search(
    query="project updates",
    filters={
        "AND": [
            {"project_id": "project-123"},
            {"department": "engineering"},
            {"status": {"ne": "archived"}}
        ]
    }
)
```
**示例 7: 模式匹配**
```python
# 查找来自特定域名邮箱地址的记忆
results = memory.search(
    query="contact information",
    filters={"email": {"ilike": "%@company.com"}}
)
```
#### 注意事项 {#notes}

1. **过滤器优先级**：当同时提供 `user_id`/`agent_id`/`run_id` 参数和 `filters` 时，它们会被合并。如果存在冲突，显式参数具有优先级。

2. **存储后端支持**：
   - **OceanBase**：支持所有操作符和复杂逻辑（AND/OR）
   - **SQLite**：仅支持简单的等值过滤
   - **PostgreSQL**：仅支持简单的等值过滤

3. **性能**：过滤器在数据库层面应用以优化性能。使用过滤器在语义搜索之前缩小结果范围。

4. **元数据字段**：自定义元数据字段以 JSON 格式存储，可以使用与标准字段相同的语法进行过滤。

#### get(memory_id, user_id=None, agent_id=None) {#getmemory_id-user_idnone-agent_idnone}

通过 ID 获取特定记忆。

**参数:**
- `memory_id` (int): 记忆标识符。
- `user_id` (str, optional): 用于权限检查的用户标识符。
- `agent_id` (str, optional): 用于权限检查的 Agent 标识符。

**返回值:**
- `dict | None`: 记忆数据或如果未找到则返回 None。

**示例:**
```python
memory_data = memory.get(123, user_id="user123")
if memory_data:
    print(f"Content: {memory_data.get('memory', '')}")
```
#### update(memory_id, content=None, user_id=None, agent_id=None, metadata=None) {#updatememory_id-contentnone-user_idnone-agent_idnone-metadatanone}

更新一个已存在的记忆。

**参数:**
- `memory_id` (int): 记忆标识符。
- `content` (str, optional): 新的记忆内容。
- `user_id` (str, optional): 用于权限检查的用户标识符。
- `agent_id` (str, optional): 用于权限检查的 Agent 标识符。
- `metadata` (dict, optional): 更新的元数据。

**返回值:**
- `dict`: 更新后的记忆数据。

**示例:**
```python
updated = memory.update(
    memory_id=123,
    content="User prefers Python over Java",
    user_id="user123",
    metadata={"updated_at": "2024-01-01"}
)
```
#### delete(memory_id, user_id=None, agent_id=None) {#deletememory_id-user_idnone-agent_idnone}

通过 ID 删除记忆。

**参数:**
- `memory_id` (int): 记忆标识符。
- `user_id` (str, 可选): 用于权限检查的用户标识符。
- `agent_id` (str, 可选): 用于权限检查的 Agent 标识符。

**返回值:**
- `bool`: 如果删除成功返回 True，否则返回 False。

**示例:**
```python
success = memory.delete(123, user_id="user123")
```
#### delete_all(user_id=None, agent_id=None, run_id=None) {#delete_alluser_idnone-agent_idnone-run_idnone}

删除符合条件的所有记忆。

**参数:**
- `user_id` (str, 可选): 按用户ID筛选。
- `agent_id` (str, 可选): 按 Agent ID筛选。
- `run_id` (str, 可选): 按运行ID筛选。

**返回值:**
- `dict`: 包含删除数量的结果。

**示例:**
```python
result = memory.delete_all(user_id="user123")
print(f"Deleted {result.get('count', 0)} memories")
```
#### get_all(user_id=None, agent_id=None, run_id=None, limit=100, offset=0, filters=None) {#get_alluser_idnone-agent_idnone-run_idnone-limit100-offset0-filtersnone}

检索符合条件的所有记忆。

**参数:**
- `user_id` (str, optional): 按用户 ID 过滤。
- `agent_id` (str, optional): 按 Agent ID 过滤。
- `run_id` (str, optional): 按运行 ID 过滤。
- `limit` (int): 返回结果的最大数量（默认值: 100）。
- `offset` (int): 分页的偏移量（默认值: 0）。
- `filters` (dict, optional): 用于高级过滤的元数据过滤器。详细文档请参阅上方的 [Filter Parameter Format](#filter-parameter-format)。

**返回值:**
- `dict`: 所有匹配的记忆。格式：
  ```python
  {
      "results": [
          {
              "id": 123,
              "memory": "content",
              "metadata": {...},
              ...
          }
      ],
      "relations": [...]  # 如果启用了图存储
  }
  ```
**示例:**
```python
# 简单检索
all_memories = memory.get_all(user_id="user123", limit=50, offset=0)
for mem in all_memories.get('results', []):
    print(f"- {mem.get('memory', '')}")

# 使用高级过滤
all_memories = memory.get_all(
    user_id="user123",
    filters={
        "category": "food",
        "rating": {"gte": 4.0}
    },
    limit=50,
    offset=0
)
```
### 智能记忆功能 {#intelligent-memory-features}

当 `infer=True`（默认值）时，Memory 会自动执行以下操作：

- **事实提取**：从对话中提取事实
- **重复检测**：防止重复记忆
- **记忆更新**：当信息发生变化时更新现有记忆
- **冲突解决**：处理矛盾信息
- **记忆整合**：合并相关记忆

**注意：** 智能处理默认启用。设置 `infer=False` 可禁用智能处理，仅用于简单存储操作。

有关智能记忆功能的更多详细信息，请参阅 [入门指南](../guides/0001-getting_started.md)。

### 错误处理 {#error-handling}

所有方法可能会引发异常。常见错误包括：

- `ValueError`：参数无效
- `ConnectionError`：存储后端连接问题
- `RuntimeError`：LLM 或 embedding 服务错误

**示例：**
```python
try:
    result = memory.add(memory="Test", user_id="user123")
except Exception as e:
    print(f"Error: {e}")
```
