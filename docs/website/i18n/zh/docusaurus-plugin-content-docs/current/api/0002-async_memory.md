# AsyncMemory API 参考 {#asyncmemory-api-reference}

`AsyncMemory` 为高性能和并发场景提供异步记忆操作。

## 类: AsyncMemory {#class-asyncmemory}
```python
from powermem import AsyncMemory

async_memory = AsyncMemory(config=config)
await async_memory.initialize()
```
### 构造函数 {#constructor}

#### AsyncMemory(config=None, storage_type=None, llm_provider=None, embedding_provider=None) {#asyncmemoryconfignone-storage_typenone-llm_providernone-embedding_providernone}

创建一个新的 AsyncMemory 实例。

**参数:**
- `config` (dict, optional): 配置字典。
- `storage_type` (str, optional): 存储后端类型（已弃用）。
- `llm_provider` (str, optional): LLM 提供商名称（已弃用）。
- `embedding_provider` (str, optional): Embedding 提供商名称（已弃用）。

**注意:** 创建后必须调用 `await async_memory.initialize()`。

### 初始化 {#initialization}

#### initialize() {#initialize}

初始化异步记忆组件。创建后必须调用。

**示例:**
```python
async_memory = AsyncMemory(config=config)
await async_memory.initialize()
```
### 异步方法 {#async-methods}

所有异步方法的接口与 `Memory` 相同，但它们是异步的。

#### async add(messages, user_id=None, agent_id=None, run_id=None, metadata=None, filters=None, infer=True) {#async-addmessages-user_idnone-agent_idnone-run_idnone-metadatanone-filtersnone-infertrue}

异步添加记忆。

**参数:**
- `messages` (str | dict | list[dict]): 记忆内容。可以是：
  - 一个字符串（将被转换为消息）
  - 一个包含 `role` 和 `content` 的单条消息字典
  - 一个 OpenAI 格式的消息字典列表
- `user_id` (str, optional): 用户标识符。
- `agent_id` (str, optional): Agent 标识符。
- `run_id` (str, optional): 运行/会话标识符。
- `metadata` (dict, optional): 附加元数据。
- `filters` (dict, optional): 用于高级过滤的元数据筛选器。
- `infer` (bool): 启用智能记忆处理（默认值: True）。

**返回值:**
- `dict`: 包含已添加记忆信息的结果。

**示例:**
```python
result = await async_memory.add(
    messages="User likes async programming",
    user_id="user123"
)
```
#### async search(query, user_id=None, agent_id=None, run_id=None, filters=None, limit=30, threshold=None) {#async-searchquery-user_idnone-agent_idnone-run_idnone-filtersnone-limit30-thresholdnone}

异步搜索记忆。

**参数:**
- `query` (str): 搜索查询字符串。
- `user_id` (str, optional): 按用户 ID 过滤。
- `agent_id` (str, optional): 按 Agent ID 过滤。
- `run_id` (str, optional): 按运行 ID 过滤。
- `filters` (dict, optional): 用于高级过滤的元数据过滤器。
- `limit` (int): 返回结果的最大数量（默认值: 30）。
- `threshold` (float, optional): 用于过滤结果的相似度阈值（0.0-1.0）。

**返回值:**
- `dict`: 包含记忆和分数的搜索结果。

**示例:**
```python
results = await async_memory.search(
    query="user preferences",
    user_id="user123",
    limit=5,
    threshold=0.7
)
```
#### async get(memory_id, user_id=None, agent_id=None) {#async-getmemory_id-user_idnone-agent_idnone}

异步检索记忆。

**参数:**
- `memory_id` (int): 记忆标识符。
- `user_id` (str, optional): 用于权限检查的用户标识符。
- `agent_id` (str, optional): 用于权限检查的 Agent 标识符。

**返回值:**
- `dict | None`: 记忆数据，如果未找到则返回 None。

**示例:**
```python
memory_data = await async_memory.get(123, user_id="user123")
```
#### async update(memory_id, content=None, user_id=None, agent_id=None, metadata=None) {#async-updatememory_id-contentnone-user_idnone-agent_idnone-metadatanone}

异步更新记忆。

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
updated = await async_memory.update(
    memory_id=123,
    content="Updated content",
    user_id="user123"
)
```
#### async delete(memory_id, user_id=None, agent_id=None) {#async-deletememory_id-user_idnone-agent_idnone}

异步删除一条记忆。

**参数:**
- `memory_id` (int): 记忆标识符。
- `user_id` (str, 可选): 用于权限检查的用户标识符。
- `agent_id` (str, 可选): 用于权限检查的 Agent 标识符。

**返回值:**
- `bool`: 如果删除成功返回 True，否则返回 False。

**示例:**
```python
success = await async_memory.delete(123, user_id="user123")
```
#### async delete_all(user_id=None, agent_id=None, run_id=None) {#async-delete_alluser_idnone-agent_idnone-run_idnone}

异步删除所有匹配的记忆。

**示例：**
```python
result = await async_memory.delete_all(user_id="user123")
```
#### async get_all(user_id=None, agent_id=None, run_id=None, limit=100, offset=0, filters=None) {#async-get_alluser_idnone-agent_idnone-run_idnone-limit100-offset0-filtersnone}

异步检索所有匹配的记忆。

**参数:**
- `user_id` (str, optional): 根据用户 ID 进行筛选。
- `agent_id` (str, optional): 根据 Agent ID 进行筛选。
- `run_id` (str, optional): 根据运行 ID 进行筛选。
- `limit` (int): 返回结果的最大数量（默认值: 100）。
- `offset` (int): 分页偏移量（默认值: 0）。
- `filters` (dict, optional): 用于高级筛选的元数据过滤器。

**返回值:**
- `dict`: 所有匹配的记忆。

**示例:**
```python
all_memories = await async_memory.get_all(user_id="user123", limit=50, offset=0)
```
### 使用示例 {#usage-examples}

#### 基本异步用法 {#basic-async-usage}
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

            # 添加记忆
            await async_memory.add(
                messages="User prefers async operations",
                user_id="user123"
            )

            # 搜索
            results = await async_memory.search(
                query="user preferences",
                user_id="user123"
            )

    print(f"Found {len(results.get('results', []))} memories")

asyncio.run(main())
```
#### 并发操作 {#concurrent-operations}
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def add_multiple_memories():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 并发添加多条记忆
    tasks = [
        async_memory.add(messages=f"Memory {i}", user_id="user123")
        for i in range(10)
    ]

    results = await asyncio.gather(*tasks)
    print(f"Added {len(results)} memories")

asyncio.run(add_multiple_memories())
```
#### 批量处理 {#batch-processing}
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def batch_process():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    messages_list = [
        [{"role": "user", "content": f"Message {i}"}]
        for i in range(100)
    ]

    # 分批处理
    batch_size = 10
    for i in range(0, len(messages_list), batch_size):
        batch = messages_list[i:i+batch_size]
        tasks = [
            async_memory.add(messages=msg, user_id="user123", infer=True)
            for msg in batch
        ]
        await asyncio.gather(*tasks)
        print(f"Processed batch {i//batch_size + 1}")

asyncio.run(batch_process())
```
### 限制：嵌入式 seekdb 不支持异步 {#limitation-embedded-seekdb-does-not-support-async}

嵌入式 seekdb（未配置 `host` 的本地文件模式）使用的是单线程的 C++ 引擎，**不支持并发多线程访问**。`AsyncMemory` 在内部通过 `ThreadPoolExecutor` 提交同步操作，这会导致多个线程同时读写同一个嵌入式 seekdb 实例。这可能引发 C++ 层级的崩溃，例如 `pure virtual method called` 或 `Segmentation fault`。

**`AsyncMemory` 不能与嵌入式 seekdb 一起使用。** 请改用同步的 `Memory` 类。
```python
# ❌ embedded seekdb 不支持
from powermem import AsyncMemory
async_memory = AsyncMemory(config=embedded_seekdb_config)  # 会崩溃

# ✓ embedded seekdb 请使用同步接口
from powermem import Memory
memory = Memory(config=embedded_seekdb_config)
```
远程 OceanBase（配置了 `host`）不受此限制影响，并完全支持 `AsyncMemory`。

### 何时使用 AsyncMemory {#when-to-use-asyncmemory}

在以下情况下使用 `AsyncMemory`：
- 同时处理大量记忆
- 构建异步 Web 应用程序（如 FastAPI、aiohttp）
- 实现批处理管道
- 需要非阻塞的记忆操作
- 使用 **远程 OceanBase**（配置了 `host`）

在以下情况下使用 `Memory`：
- 简单的同步脚本
- 交互式笔记本
- 无并发需求的简单用例
- 使用 **嵌入式 seekdb**（本地文件模式，无 `host`）