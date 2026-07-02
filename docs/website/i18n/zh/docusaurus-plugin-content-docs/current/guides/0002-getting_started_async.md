---
title: 异步入门
sidebar_label: 异步入门
---

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem（`pip install powermem`）
- 基本了解 Python 的 async/await 语法

## 何时使用 Async Memory？ {#when-to-use-async-memory}

当您需要以下场景时，powermem 的异步版本（`AsyncMemory`）是理想选择：

- **高吞吐量应用**：同时处理大量记忆操作
- **并发操作**：同时添加、搜索或更新多个记忆
- **I/O 密集型工作负载**：操作涉及网络调用（如 API 请求、数据库查询）
- **非阻塞操作**：在处理记忆时保持应用程序的响应性

### Async 与 Sync：应该选择哪一个？ {#async-vs-sync-which-should-you-use}

- **使用 `AsyncMemory`（异步）** 适用于：
  - 高并发场景
  - 批量处理大型数据集
  - 需要最大吞吐量时

> **注意：** 如果您是 powermem 的新手，请先从 [同步指南](./0001-getting_started.md) 开始学习基础知识，然后再回到这里了解异步操作。

## 配置 {#configuration}

在使用异步记忆之前，您需要配置 powermem。同步和异步版本的配置方式相同。Powermem 可以自动从项目目录中的 `.env` 文件加载配置。

### 创建 .env 文件 {#creating-a-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并配置您的设置：
   - **LLM Provider**: 选择您的语言模型提供商
   - **Embedding Provider**: 选择文本如何转换为向量
   - **Vector Store**: 选择您的数据库（开发环境使用 SQLite，生产环境使用 OceanBase）

> **注意：** `auto_config()` 函数会自动：
> - 在当前目录中查找 `.env` 文件
> - 从环境变量加载配置
> - 如果未找到配置，则使用合理的默认值

有关更多配置选项，请参阅 `.env.example` 中的完整示例或参考 [配置指南](./0003-configuration.md)。

## 理解异步操作 {#understanding-async-operations}

在 powermem 中，异步操作提供了几个关键优势：

### 主要优势 {#key-benefits}

- **非阻塞**：操作不会阻塞事件循环，允许其他任务运行
- **并发处理**：可以同时执行多个记忆操作
- **高吞吐量**：并行处理大量操作，显著提高性能
- **更好的 I/O 性能**：非常适合涉及网络调用或数据库查询的操作
- **可扩展性**：无需创建多个线程即可处理更多并发请求

### Powermem 中的异步工作原理 {#how-async-works-in-powermem}

1. **异步方法**：所有记忆操作（`add`、`search`、`update`、`delete`）都是异步的
2. **事件循环**：操作运行在 Python 的 asyncio 事件循环上
3. **并发执行**：使用 `asyncio.gather()` 同时运行多个操作
4. **资源管理**：正确初始化和清理异步资源

### 性能注意事项 {#performance-considerations}

- **I/O 密集型操作**：异步在等待网络或数据库响应时表现出色
- **CPU 密集型操作**：对于 CPU 密集型任务，考虑使用 `asyncio.to_thread()`
- **批处理大小**：处理大量项目时，使用批处理以避免系统过载
- **连接池**：异步操作可以高效地共享数据库连接

## 初始化异步记忆 {#initialize-async-memory}

使用异步记忆的第一步是创建并初始化一个 `AsyncMemory` 实例。与同步版本不同，异步记忆需要显式初始化。

### 理解 AsyncMemory 初始化 {#understanding-asyncmemory-initialization}

`AsyncMemory` 类：
- 需要通过 `await async_memory.initialize()` 显式初始化
- 设置与向量存储和嵌入服务的异步连接
- 必须在执行任何操作之前初始化
- 完成后应正确关闭（尽管 Python 的垃圾回收器会处理这一点）

### 重要提示：始终初始化 {#important-always-initialize}

与同步的 `Memory` 类不同，`AsyncMemory` 要求您在使用前调用 `initialize()`。这是因为：
- 需要建立异步连接
- 某些向量存储需要异步连接池
- 嵌入服务可能需要异步 HTTP 客户端

让我们创建并初始化一个 AsyncMemory 实例：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    # 从 .env 文件加载配置
    config = auto_config()

    # 创建 AsyncMemory 实例
    async_memory = AsyncMemory(config=config)

    # 初始化（异步模式必需）
    await async_memory.initialize()

    print("✓ AsyncMemory initialized successfully!")

    # 在这里执行异步操作
    # 完成后记得关闭（可选但推荐）
    # await async_memory.close()

# 运行异步函数
asyncio.run(main())
```
### 使用 JSON/字典配置 {#using-jsondictionary-configuration}

除了使用 `.env` 文件外，您还可以直接以 Python 字典（类似 JSON 的格式）传递配置。这在以下情况下非常有用：
- 您希望从 JSON 文件加载配置
- 您需要以编程方式生成配置
- 您将配置嵌入到应用程序代码中

以下是使用字典配置的示例：
```python
import asyncio
from powermem import AsyncMemory

async def main():
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

    # 使用字典配置创建 AsyncMemory 实例
    async_memory = AsyncMemory(config=config)

    # 初始化（异步模式必需）
    await async_memory.initialize()

    print("✓ AsyncMemory initialized with JSON config!")

    # 在这里执行异步操作
    # await async_memory.close()

# 运行异步函数
asyncio.run(main())
```
您还可以从 JSON 文件加载配置：
```python
import asyncio
import json
from json import dump, dumps
from powermem import AsyncMemory

async def main():
    # 从 JSON 文件加载配置
    with open('config.json', 'r') as f:
        config = json.load(f)

    # 创建 AsyncMemory 实例
    async_memory = AsyncMemory(config=config)

    # 初始化（异步模式必需）
    await async_memory.initialize()

    print("✓ AsyncMemory initialized from JSON file!")

    # 在这里执行异步操作
    # await async_memory.close()

# 运行异步函数
asyncio.run(main())
```
> **注意：** 使用字典/JSON 配置时，请确保包含所有必需字段（`llm`、`embedder`、`vector_store`），以及它们各自的 `provider` 和 `config` 部分。有关更多配置选项，请参阅[配置指南](./0003-configuration.md)。

> **提示：** 在生产应用中，建议使用异步上下文管理器或确保正确的清理操作。`initialize()` 方法会设置连接，这些连接在应用关闭时应理想地被关闭。

## 异步添加记忆 {#add-memories-asynchronously}

异步添加记忆与同步版本类似，但使用 `await` 来处理异步操作。这允许您的应用在存储记忆的同时继续处理其他任务。

### 理解异步添加操作 {#understanding-async-add-operations}

当您调用 `await async_memory.add()` 时：
1. 操作会被调度到事件循环中
2. 您的代码可以将控制权交给其他任务
3. 记忆会被异步处理（嵌入、存储）
4. 操作完成后返回结果

### 与同步版本的主要区别 {#key-differences-from-sync-version}

- **使用 `await`**：所有操作必须使用 `await`
- **在异步函数中调用**：必须从异步函数中调用
- **非阻塞**：等待期间其他异步任务可以运行
- **相同参数**：接受与同步版本相同的参数

### 处理结果 {#handling-results}

结果格式与同步版本相同：
- 返回一个包含 `results` 列表的字典
- 每个结果包含记忆 ID、内容和元数据
- 空的结果列表可能表示发生了去重

让我们异步添加一个记忆：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 异步添加记忆
    result = await async_memory.add(
        "User likes Python programming",  # messages 参数（第一个位置参数）
        user_id="user123"
    )

    # 处理结果：检查结果列表是否非空
    results_list = result.get('results', [])
    if results_list:
        memory_id = results_list[0].get('id', 'N/A')
        print(f"✓ Memory added! ID: {memory_id}")
    else:
        print("✓ Memory operation completed (may have been deduplicated)")

asyncio.run(main())
```
> **注意：** 异步的 `add()` 方法与同步版本的工作方式完全相同，但不会阻塞您的事件循环。这意味着在处理记忆的同时，其他异步操作可以并发运行。

## 并发记忆操作 {#concurrent-memory-operations}

异步操作的最大优势之一是能够并发处理多个记忆。与其按顺序等待每个操作完成，不如同时运行它们。

### 为什么并发操作很重要 {#why-concurrent-operations-matter}

- **速度**：同时处理多个操作，而不是一个接一个地处理
- **效率**：更好地利用 I/O 资源（网络、数据库）
- **吞吐量**：每秒处理更多操作
- **可扩展性**：对于高性能应用至关重要

### 并发执行的工作原理 {#how-concurrent-execution-works}

1. **创建任务**：构建协程对象的列表（暂时不要 `await` 它们）
2. **收集任务**：使用 `asyncio.gather()` 并发运行所有任务
3. **等待完成**：所有任务并行执行
4. **收集结果**：从所有操作中获取结果

### 理解 asyncio.gather() {#understanding-asynciogather}

- **并发执行**：所有任务同时运行
- **结果顺序**：结果按照输入任务的顺序返回
- **错误处理**：如果任何任务失败，您可以选择取消其他任务或继续
- **资源限制**：注意连接限制（数据库、API 速率限制）

### 最佳实践 {#best-practices}

- **批量大小**：不要一次创建过多的并发任务
- **速率限制**：使用外部服务时遵守 API 速率限制
- **错误处理**：使用 try-except 包裹以处理单个任务的失败
- **资源管理**：监控连接池的使用情况

让我们并发添加多个记忆：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 并发添加多条记忆
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue"
    ]

    # 创建用于并发执行的任务
    # 注意：这里创建的是协程对象，暂不 await
    tasks = [
        async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
        for mem in memories
    ]

    # 并发执行所有任务
    # 这会并行运行所有 add 操作
    results = await asyncio.gather(*tasks)

    print(f"✓ Added {len(results)} memories concurrently")

    # 按需处理单个结果
    for i, result in enumerate(results):
        results_list = result.get('results', [])
        if results_list:
            print(f"  Memory {i+1}: {results_list[0].get('memory', 'N/A')}")

asyncio.run(main())
```
> **提示：** `*tasks` 语法会解包任务列表。这使得 `asyncio.gather()` 可以将多个协程对象作为单独的参数接收。所有操作将并发执行，比顺序执行快得多。

## 异步搜索 {#async-search}

异步搜索记忆允许您在不阻塞应用程序的情况下执行语义搜索。这在需要同时处理多个搜索请求的 Web 应用程序中尤其有用。

### 理解异步搜索 {#understanding-async-search}

异步搜索的工作方式与同步搜索相同，但具有以下特点：
- **非阻塞**：搜索时不会阻塞事件循环
- **并发查询**：可以同时处理多个搜索请求
- **相同语义**：使用相同的语义搜索算法
- **更高性能**：适用于高并发场景

### 异步搜索的性能 {#search-performance-with-async}

- **多用户**：同时处理来自多个用户的搜索请求
- **并行搜索**：同时运行多个搜索
- **资源共享**：高效共享数据库连接
- **响应时间**：在负载下提供更好的响应时间

### 异步搜索的使用场景 {#use-cases-for-async-search}

- **Web API**：并行处理多个搜索请求
- **实时应用**：在保持响应能力的同时进行搜索
- **批量查询**：同时对多个用户进行搜索
- **分析**：并行运行多个分析查询

让我们异步搜索记忆：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 先添加一些记忆
    await async_memory.add("User likes Python", user_id="user123")
    await async_memory.add("User prefers email", user_id="user123")

    # 异步搜索
    results = await async_memory.search(
        query="user preferences",
        user_id="user123",
        limit=5
    )

    print(f"Found {len(results.get('results', []))} memories:")
    for result in results.get('results', []):
        print(f"  - {result['memory']}")

asyncio.run(main())
```
> **注意：** 您也可以使用 `asyncio.gather()` 并发运行多个搜索，就像使用 `add()` 操作一样。当您需要同时搜索不同用户或不同查询时，这非常有用。

## 批量处理 {#batch-processing}

在处理大型数据集时，重要的是以批量方式处理记忆，而不是一次性全部处理。这可以防止系统过载，并有助于高效管理资源。

### 为什么要进行批量处理？ {#why-batch-processing}

- **资源管理**：避免耗尽数据库连接或触发 API 速率限制
- **记忆效率**：以可管理的块处理数据
- **进度跟踪**：更容易跟踪进度并处理错误
- **速率限制**：在使用外部服务时遵守 API 速率限制
- **错误恢复**：如果某一批次失败，其他批次仍然可以成功

### 批量处理策略 {#batch-processing-strategy}

1. **分成批次**：将数据分割成较小的块
2. **并发处理**：在每个批次内并发处理项目
3. **等待完成**：等待每个批次完成后再开始下一个批次
4. **处理错误**：为失败的批次实现错误处理

### 如何选择批量大小 {#choosing-batch-size}

最佳批量大小取决于以下因素：
- **API 速率限制**：您的 API 每秒允许的请求数量
- **数据库连接**：连接池大小
- **记忆约束**：系统可用记忆量
- **网络带宽**：您的网络容量

常见的批量大小：
- **小批量 (5-10)**：适用于速率受限的 API
- **中等批量 (10-50)**：适用于大多数用例
- **大批量 (50-100)**：适用于高容量系统

### 最佳实践 {#best-practices-1}

- **监控性能**：根据实际性能调整批量大小
- **处理失败**：为失败的批次实现重试逻辑
- **进度报告**：记录进度以跟踪长时间运行的操作
- **资源清理**：确保在批次之间正确清理资源

让我们以批量方式处理记忆：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 大量记忆列表
    memories = [
        f"Memory {i}: User preference {i}"
        for i in range(100)
    ]

    # 分批处理
    batch_size = 10
    total_batches = (len(memories) + batch_size - 1) // batch_size

    for i in range(0, len(memories), batch_size):
        batch = memories[i:i+batch_size]
        batch_num = i // batch_size + 1

        # 为当前批次创建任务
        tasks = [
            async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
            for mem in batch
        ]

        # 并发执行当前批次
        try:
            results = await asyncio.gather(*tasks)
            print(f"✓ Processed batch {batch_num}/{total_batches} ({len(results)} memories)")
        except Exception as e:
            print(f"✗ Error processing batch {batch_num}: {e}")

asyncio.run(main())
```
> **提示：** 对于生产环境的应用程序，可以考虑添加：
> - 进度条或日志记录
> - 失败操作的重试逻辑
> - 速率限制以遵守 API 限制
> - 检查点以便从失败中恢复

## 使用异步操作与智能记忆 {#async-with-intelligent-memory}

智能记忆功能可以无缝地与异步操作配合使用。这使您能够利用 PowerMem 的智能事实提取和去重功能，同时通过异步操作保持高性能。

### 什么是智能记忆？ {#what-is-intelligent-memory}

智能记忆（`infer=True`）支持以下功能：
- **自动事实提取**：自动从对话中提取事实
- **智能去重**：防止存储重复或非常相似的记忆
- **上下文理解**：更好地理解对话历史中的上下文
- **多轮对话**：有效处理对话线程

### 异步 + 智能记忆的优势 {#async--intelligent-memory-benefits}

- **非阻塞处理**：用于事实提取的 LLM 调用不会阻塞您的事件循环
- **并发处理**：同时处理多个对话
- **更高吞吐量**：每秒处理更多智能记忆操作
- **可扩展性**：高效扩展智能记忆处理

### 何时使用智能记忆 {#when-to-use-intelligent-memory}

- **对话处理**：在处理聊天记录或对话时
- **自动提取**：当您希望自动提取事实时
- **去重**：当您需要防止重复记忆时
- **复杂上下文**：当记忆依赖于对话上下文时

### 性能注意事项 {#performance-considerations-1}

- **LLM 调用**：智能记忆需要调用 LLM API，这些调用是 I/O 密集型的（非常适合异步）
- **处理时间**：比简单的添加操作需要更长时间
- **成本**：每次智能操作都会使用 LLM 令牌
- **并发限制**：注意 LLM API 的速率限制

让我们使用异步操作与智能记忆功能：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 使用智能处理添加记忆
    # 这会自动从对话中提取事实
    result = await async_memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "Nice to meet you!"}
        ],
        user_id="user123",
        infer=True  # 启用智能事实提取
    )

    print(f"✓ Processed conversation, extracted {len(result.get('results', []))} memories:")
    for mem in result.get('results', []):
        print(f"  - {mem.get('memory', '')}")

asyncio.run(main())
```
> **注意：** 智能记忆操作非常适合异步处理，因为它们涉及对LLM API的网络调用。异步处理允许这些操作并发运行，在处理多个对话时显著提高吞吐量。

## 异步更新和删除 {#async-update-and-delete}

异步更新和删除记忆遵循与其他异步操作相同的模式。这些操作在需要同时处理多个更新/删除请求的Web应用程序中特别有用。

### 理解异步更新 {#understanding-async-update}

`update()` 方法：
- **需要记忆ID**：需要创建记忆时的ID
- **重新生成Embedding**：为更新的内容创建新的向量Embedding
- **非阻塞**：更新时不会阻塞
- **相同参数**：接受与同步版本相同的参数

### 理解异步删除 {#understanding-async-delete}

`delete()` 方法：
- **需要记忆ID**：需要指定要删除的记忆ID
- **永久性**：删除是永久性的（与同步版本相同）
- **返回布尔值**：成功时返回 `True`，失败时返回 `False`
- **非阻塞**：以异步方式执行

### 异步更新/删除的使用场景 {#use-cases-for-async-updatedelete}

- **Web API**：同时处理多个更新/删除请求
- **批量操作**：并行更新或删除多个记忆
- **实时应用**：在不阻塞应用程序的情况下更新记忆
- **数据管理**：高效管理大量记忆

### 最佳实践 {#best-practices-2}

- **错误处理**：始终检查返回值并处理错误
- **并发操作**：可以同时更新/删除多个记忆
- **资源清理**：确保在长时间运行的应用程序中进行适当的清理
- **验证**：在操作之前验证记忆ID

让我们异步更新和删除记忆：
```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 添加记忆（演示时使用 infer=False 确保写入）
    # 生产环境中可使用 infer=True 进行智能去重
    result = await async_memory.add(
        "User likes Python",  # messages 作为第一个位置参数
        user_id="user123",
        infer=False  # 演示时禁用智能去重
    )

    # 处理结果：检查结果列表是否非空
    results_list = result.get('results', [])
    if not results_list:
        print("Error: No memory was added")
        raise ValueError("Cannot update/delete: memory was not added")

    memory_id = results_list[0].get('id')
    if not memory_id:
        print("Error: Memory ID not found in result")
        raise ValueError("Cannot update/delete: memory ID not found")

    # 异步更新记忆
    updated = await async_memory.update(
        memory_id=memory_id,
        content="User loves Python programming"
    )
    print(f"✓ Updated memory: {updated.get('memory', 'N/A')}")

    # 异步删除记忆
    success = await async_memory.delete(memory_id)
    if success:
        print(f"✓ Deleted memory {memory_id}")
    else:
        print(f"✗ Failed to delete memory {memory_id}")

asyncio.run(main())
```
### 并发更新/删除操作 {#concurrent-updatedelete-operations}

您还可以同时执行多个更新或删除操作：
```python
# 示例：并发删除多条记忆
memory_ids = ["id1", "id2", "id3"]
tasks = [async_memory.delete(mid) for mid in memory_ids]
results = await asyncio.gather(*tasks)
success_count = sum(1 for r in results if r)
print(f"✓ Deleted {success_count}/{len(memory_ids)} memories")
```
> **提示：** 与其他异步操作类似，您可以使用 `asyncio.gather()` 并发执行多个更新或删除操作。这在管理大量记忆或处理批量操作时尤其有用。
