# 场景 4：异步操作 {#scenario-4-async-operations}

本场景演示如何使用 PowerMem 的异步操作来实现高性能的并发记忆操作。

## 前置条件 {#prerequisites}

- 已完成场景 1
- 理解 Python 中的 async/await
- 已安装 PowerMem

## 理解异步操作 {#understanding-async-operations}

异步操作可以实现：
- 非阻塞的记忆操作
- 并发处理
- 高吞吐量场景
- 对 I/O 密集型操作的更好性能

## 步骤 1：初始化异步记忆 {#step-1-initialize-async-memory}

首先，让我们创建并初始化一个 AsyncMemory 实例：
```python
# async_operations_example.py
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()

    # 创建 AsyncMemory 实例
    async_memory = AsyncMemory(config=config)

    # 初始化（异步模式必需）
    await async_memory.initialize()

    print("✓ AsyncMemory initialized successfully!")

# 运行异步函数
asyncio.run(main())
```
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**
```
✓ AsyncMemory initialized successfully!
```
## 第 2 步：异步添加记忆 {#step-2-add-memories-asynchronously}

使用异步方法添加记忆：
```python
# async_operations_example.py
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

    # 处理结果，检查 results 列表是否非空
    results_list = result.get('results', [])
    if results_list:
        memory_id = results_list[0].get('id', 'N/A')
        print(f"✓ Memory added! ID: {memory_id}")
    else:
        print("✓ Memory operation completed (may have been deduplicated)")

asyncio.run(main())
```
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**

在本示例中，我们将展示如何使用 `AsyncMemory` 来处理异步操作。以下是预期的输出结果：

1. 初始化 `AsyncMemory` 实例。
2. 使用 `add_memory` 方法异步添加新的记忆项。
3. 使用 `retrieve_memory` 方法异步检索记忆项。
4. 验证记忆项是否正确存储和检索。

以下是代码示例的运行结果：

```plaintext
Memory added successfully.
Retrieved memory: {"key": "example_key", "value": "example_value"}

通过以上步骤，您可以了解如何在异步环境中有效地使用 `AsyncMemory`。
```
```
✓ Memory added! ID: mem_xxx
```
## 第 3 步：并发记忆操作 {#step-3-concurrent-memory-operations}

同时添加多个记忆：
```python
# async_operations_example.py
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

    # 创建并发执行任务
    tasks = [
        async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
        for mem in memories
    ]

    # 并发执行所有任务
    results = await asyncio.gather(*tasks)

    print(f"✓ Added {len(results)} memories concurrently")

asyncio.run(main())
```
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**
```
✓ Added 4 memories concurrently
```
## 第4步：异步搜索 {#step-4-async-search}

异步搜索记忆：
```python
# async_operations_example.py
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
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**
```
Found 2 memories:
  - User prefers email
  - User likes Python
```
## 第五步：批量处理 {#step-5-batch-processing}

以批量方式处理记忆：
```python
# async_operations_example.py
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
    for i in range(0, len(memories), batch_size):
        batch = memories[i:i+batch_size]

        # 为批次创建任务
        tasks = [
            async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
            for mem in batch
        ]

        # 并发执行批次
        await asyncio.gather(*tasks)

        print(f"✓ Processed batch {i//batch_size + 1}")

asyncio.run(main())
```
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**

在本场景中，我们将演示如何使用 `AsyncMemory` 来处理异步操作。以下是预期的输出：

1. 初始化 `AsyncMemory`。
2. 向 `AsyncMemory` 添加多个 `MemoryItem`。
3. 异步检索存储的 `MemoryItem`。
4. 验证检索到的 `MemoryItem` 是否与预期一致。
```
✓ Processed batch 1
✓ Processed batch 2
✓ Processed batch 3
...
```
## 第6步：使用智能记忆的异步操作 {#step-6-async-with-intelligent-memory}

使用带有智能记忆功能的异步操作：
```python
# async_operations_example.py
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    # 使用智能处理添加记忆
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
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**
```
✓ Processed conversation, extracted 1 memories:
  - Is Alice, a software engineer
```
## 第7步：异步更新和删除 {#step-7-async-update-and-delete}

异步更新和删除记忆：
```python
# async_operations_example.py
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

    # 处理结果，检查 results 列表是否非空
    results_list = result.get('results', [])
    if not results_list:
        print("Error: No memory was added")
        raise ValueError("Cannot update/delete: memory was not added")

    memory_id = results_list[0].get('id')
    if not memory_id:
        print("Error: Memory ID not found in result")
        raise ValueError("Cannot update/delete: memory ID not found")

    # 更新记忆
    updated = await async_memory.update(
        memory_id=memory_id,
        content="User loves Python programming"
    )
    print(f"✓ Updated memory: {updated.get('memory', 'N/A')}")

    # 删除记忆
    success = await async_memory.delete(memory_id)
    if success:
        print(f"✓ Deleted memory {memory_id}")

asyncio.run(main())
```
**运行此代码：**
```bash
python async_operations_example.py
```
**预期输出：**
```
✓ Updated memory: User loves Python programming
✓ Deleted memory mem_xxx
```
## 第 8 步：FastAPI 集成示例 {#step-8-fastapi-integration-example}

在 FastAPI 中使用异步记忆：
```python
# fastapi_example.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from powermem import AsyncMemory, auto_config

config = auto_config()
async_memory = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化异步记忆
    global async_memory
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    yield
    # 关闭时清理（如有需要）
    # 如有需要，可在此清理 async_memory

app = FastAPI(lifespan=lifespan)

class MemoryRequest(BaseModel):
    memory: str
    user_id: str

@app.post("/memories")
async def add_memory(request: MemoryRequest):
    try:
        result = await async_memory.add(
            request.memory,  # messages 作为第一个位置参数
            user_id=request.user_id
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/memories/search")
async def search_memories(query: str, user_id: str):
    try:
        results = await async_memory.search(
            query=query,
            user_id=user_id
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```
## 完整示例 {#complete-example}

以下是一个完整的异步示例：
```python
# complete_async_example.py
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    print("=" * 80)
    print("Async Memory Operations Demo")
    print("=" * 80)

    # 第 1 步：并发添加记忆
    print("\n[Step 1] Adding Memories Concurrently")
    print("-" * 60)

    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer"
    ]

    tasks = [
        async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
        for mem in memories
    ]

    results = await asyncio.gather(*tasks)
    print(f"✓ Added {len(results)} memories concurrently")

    # 第 2 步：异步搜索
    print("\n[Step 2] Searching Memories")
    print("-" * 60)

    search_results = await async_memory.search(
        query="user preferences",
        user_id="user123"
    )

    print(f"Found {len(search_results.get('results', []))} memories:")
    for result in search_results.get('results', []):
        print(f"  - {result['memory']}")

    # 第 3 步：批处理
    print("\n[Step 3] Batch Processing")
    print("-" * 60)

    batch_memories = [f"Memory {i}" for i in range(20)]
    batch_size = 5

    for i in range(0, len(batch_memories), batch_size):
        batch = batch_memories[i:i+batch_size]
        tasks = [
            async_memory.add(mem, user_id="user123")  # messages 作为第一个位置参数
            for mem in batch
        ]
        await asyncio.gather(*tasks)
        print(f"✓ Processed batch {i//batch_size + 1}")

    print("\n" + "=" * 80)
    print("Demo completed successfully!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
```
**运行此代码：**
```bash
python complete_async_example.py
```
## 拓展练习 {#extension-exercises}

### 练习 1：并发搜索 {#exercise-1-concurrent-searches}

同时执行多个搜索操作：
```python
async def concurrent_searches():
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()

    queries = [
        "user preferences",
        "user interests",
        "user information"
    ]

    tasks = [
        async_memory.search(query=q, user_id="user123")
        for q in queries
    ]

    results = await asyncio.gather(*tasks)
    return results
```
### 练习 2：带错误处理的异步操作 {#exercise-2-async-with-error-handling}

为异步操作添加错误处理：
```python
async def safe_add(memory, user_id):
    try:
        result = await async_memory.add(memory, user_id=user_id)  # messages 作为第一个位置参数
        return result
    except Exception as e:
        print(f"Error adding memory: {e}")
        return None
```
### 练习 3：速率限制 {#exercise-3-rate-limiting}

为异步操作实现速率限制：
```python
import asyncio

async def rate_limited_add(memories, user_id, rate=5):
    semaphore = asyncio.Semaphore(rate)

    async def add_with_limit(memory):
        async with semaphore:
            return await async_memory.add(memory, user_id=user_id)  # messages 作为第一个位置参数

    tasks = [add_with_limit(mem) for mem in memories]
    return await asyncio.gather(*tasks)
```
## 何时使用 Async {#when-to-use-async}

在以下情况下使用 `AsyncMemory`：
- 同时处理大量记忆
- 构建异步 Web 应用程序（如 FastAPI、aiohttp）
- 实现批处理管道
- 需要非阻塞操作

在以下情况下使用 `Memory`：
- 简单的同步脚本
- 交互式笔记本
- 无并发的简单用例