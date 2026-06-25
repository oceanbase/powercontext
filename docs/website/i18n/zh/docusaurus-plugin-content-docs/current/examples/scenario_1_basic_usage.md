# 场景 1：基本用法 {#scenario-1-basic-usage}

本场景将引导您了解 PowerMem 的基础知识——存储、检索和管理记忆。

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 PowerMem（`pip install powermem`）

## 配置 {#configuration}

PowerMem 可以自动从项目目录中的 `.env` 文件加载配置。这是为您的使用场景配置 PowerMem 的推荐方式。

### 创建 .env 文件 {#creating-a-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并进行配置
   ```

> **Note:** When you call `auto_config()`, powermem will automatically:
> - Look for a `.env` file in the current directory
> - Load configuration from environment variables

For more configuration options, see the full example in `.env.example` or refer to the [Configuration Guide](../guides/0003-configuration.md).

## Step 1: Setup

First, let's create a simple Python script and import powermem:

```python
# basic_usage_example.py {#basic_usage_examplepy}
from powermem import Memory, auto_config

# 加载配置（自动从 .env 加载或使用默认值） {#load-configuration-auto-loads-from-env-or-uses-defaults}
config = auto_config()

# 创建记忆实例 {#create-memory-instance}
memory = Memory(config=config)

print("✓ 记忆初始化成功！")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ 记忆初始化成功！
```

## Step 2: Add Your First Memory

Now let's add a simple memory:

```python
# basic_usage_example.py {#basic_usage_examplepy-1}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 添加一条记忆 {#add-a-memory}
result = memory.add(
    messages="User likes Python programming",
    user_id="user123"
)

# 从结果中获取记忆 ID {#get-memory-id-from-result}
results_list = result.get('results', [])
memory_id = results_list[0].get('id', 'N/A') if results_list else 'N/A'
print(f"✓ 记忆已添加！ID: {memory_id}")
```

**Run this code:**
```bash
```python
basic_usage_example.py
```
```

**Expected output:**
```
✓ 记忆已添加！ID: xxxxxx
```

## Step 3: Add Multiple Memories

Let's add several memories for a user:

```python
# basic_usage_example.py {#basic_usage_examplepy-2}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加多条记忆 {#add-multiple-memories}
memories = [
    "用户喜欢 Python 编程",
    "用户更喜欢通过电子邮件支持而不是电话",
    "用户是一名软件工程师",
    "用户最喜欢的颜色是蓝色"
]

for mem in memories:
    result = memory.add(messages=mem, user_id=user_id)
    print(f"✓ 已添加: {mem}")

print(f"\n✓ 已为用户 {user_id} 添加所有记忆")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ 已添加：用户喜欢 Python 编程
✓ 已添加：用户更喜欢电子邮件支持而非电话支持
✓ 已添加：用户的职业是软件工程师
✓ 已添加：用户最喜欢的颜色是蓝色

✓ 已为用户 user123 添加所有记忆
```

## Step 4: Search Memories

Now let's search for memories:

```python
# basic_usage_example.py {#basic_usage_examplepy-3}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 首先添加一些记忆 {#add-some-memories-first}
memory.add("用户喜欢 Python 编程", user_id=user_id)
memory.add("用户更喜欢电子邮件支持", user_id=user_id)
memory.add("用户是一名软件工程师", user_id=user_id)

# 搜索记忆 {#search-for-memories}
print("正在搜索 '用户偏好'...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    limit=5
)

print(f"\n找到 {len(results.get('results', []))} 条记忆:")
for i, result in enumerate(results.get('results', []), 1):
    print(f"  {i}. {result['memory']}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
搜索“用户偏好”...

找到 3 条记忆：
  1. 偏好电子邮件支持
  2. 喜欢 Python 编程
  3. 职业是软件工程师
```

## Step 5: Add Metadata

Let's add memories with metadata for better organization:

```python
# basic_usage_example.py {#basic_usage_examplepy-4}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加带有元数据的记忆 {#add-memories-with-metadata}
memory.add(
    messages="用户喜欢 Python 编程",
    user_id=user_id,
    metadata={
        "category": "preference",
        "importance": "high",
        "source": "conversation"
    }
)

memory.add(
    messages="用户更喜欢邮件支持",
    user_id=user_id,
    metadata={
        "category": "communication",
        "importance": "medium"
    }
)

print("✓ 已添加带有元数据的记忆")
```

## Step 6: Search with Metadata Filters

Search memories using metadata filters:

```python
# basic_usage_example.py {#basic_usage_examplepy-5}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加带有元数据的记忆 {#add-memories-with-metadata-1}
memory.add(
    messages="用户喜欢 Python 编程",
    user_id=user_id,
    metadata={"category": "preference"}
)

memory.add(
    messages="用户更喜欢邮件支持",
    user_id=user_id,
    metadata={"category": "communication"}
)

# 使用元数据过滤器进行搜索 {#search-with-metadata-filter}
# 注意：category 是从元数据中提取并存储为顶级字段 {#note-category-is-extracted-from-metadata-and-stored-as-a-top-level-field}
print("使用元数据过滤器进行搜索...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    filters={"category": "preference"}
)

print(f"\n找到 {len(results.get('results', []))} 条记忆：")
for result in results.get('results', []):
    print(f"  - {result['memory']}")
    print(f"    元数据: {result.get('metadata', {})}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
使用元数据过滤进行搜索...

找到 1 条记忆：
  - 喜欢 Python 编程
    元数据: {'last_searched_at': datetime.datetime(2025, 11, 6, 13, 9, 32, 250703), 'search_count': 4, 'category': 'preference', 'fulltext_content': 'Likes Python programming', 'access_count': 1, 'search_relevance_score': 0.25}
```

## Step 7: Get All Memories

Retrieve all memories for a user:

```python
# basic_usage_example.py {#basic_usage_examplepy-6}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一些记忆 {#add-some-memories}
memory.add("User likes Python", user_id=user_id)
memory.add("User prefers email", user_id=user_id)
memory.add("User works as engineer", user_id=user_id)

# 获取所有记忆 {#get-all-memories}
all_memories = memory.get_all(user_id=user_id)

print(f"\nTotal memories for {user_id}: {len(all_memories.get('results', []))}")
print("\nAll memories:")
for i, mem in enumerate(all_memories.get('results', []), 1):
    print(f"  {i}. {mem['memory']}")
```

**Run this code:**
```bash
```python
basic_usage_example.py
```
```

**Expected output:**
```
用户 user123 的总记忆数：3

所有记忆：
  1. 喜欢 Python 编程
  2. 更喜欢邮件支持
  3. 职业是工程师
```

## Step 8: Update a Memory

Update an existing memory:

```python
# basic_usage_example.py {#basic_usage_examplepy-7}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一条记忆（使用 infer=False 以确保行为可预测） {#add-a-memory-using-inferfalse-for-predictable-behavior}
result = memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    infer=False  # 禁用智能模式以确保行为可预测
)

# 从结果中获取记忆 ID {#get-memory-id-from-result-1}
results_list = result.get('results', [])
if not results_list:
    raise ValueError("未添加任何记忆。请检查结果: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("在结果中未找到记忆 ID")

# 更新记忆 {#update-the-memory}
updated = memory.update(
    memory_id=memory_id,
    content="User loves Python programming, especially for data science"
)

print(f"✓ 记忆已更新!")
print(f"  旧内容: User likes Python programming")
print(f"  新内容: {updated.get('data', 'N/A')}")
```

**Run this code:**
```bash
```python
basic_usage_example.py
```
```

**Expected output:**
```
✓ 记忆已更新！
  旧：用户喜欢 Python 编程
  新：用户热爱 Python 编程，特别是用于数据科学
```

## Step 9: Delete a Memory

Delete a memory:

```python
# basic_usage_example.py {#basic_usage_examplepy-8}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一条记忆（使用 infer=False 以确保行为可预测） {#add-a-memory-using-inferfalse-for-predictable-behavior-1}
result = memory.add(
    messages="用户喜欢 Python 编程",
    user_id=user_id,
    infer=False  # 禁用智能模式以确保行为可预测
)

# 从结果中获取记忆 ID {#get-memory-id-from-result-2}
results_list = result.get('results', [])
if not results_list:
    raise ValueError("未添加任何记忆。请检查结果: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("在结果中未找到记忆 ID")

# 删除记忆 {#delete-the-memory}
success = memory.delete(memory_id)

if success:
    print(f"✓ 记忆 {memory_id} 删除成功！")
else:
    print(f"✗ 删除记忆失败")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ 记忆 xxx 删除成功！
```

## Step 10: Delete All Memories

Delete all memories for a user:

```python
# basic_usage_example.py {#basic_usage_examplepy-9}
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# 添加一些记忆 {#add-some-memories-1}
memory.add("Memory 1", user_id=user_id)
memory.add("Memory 2", user_id=user_id)
memory.add("Memory 3", user_id=user_id)

# 删除前获取记忆数量 {#get-count-before-deletion}
all_memories = memory.get_all(user_id=user_id)
count_before = len(all_memories.get('results', []))

# 删除所有记忆（返回 True/False） {#delete-all-memories-returns-truefalse}
success = memory.delete_all(user_id=user_id)

if success:
    print(f"✓ 已删除 {count_before} 条记忆，用户 {user_id}")
else:
    print(f"✗ 删除记忆失败")
```

**Run this code:**
```bash
```python
basic_usage_example.py
```
```

**Expected output:**
```
✓ 已删除 x 条记忆，适用于 user123
```

## 完整示例

Here's a complete example combining all the steps:

```python
# complete_basic_example.py {#complete_basic_examplepy}
from powermem import Memory, auto_config

def main():
    # 加载配置
    config = auto_config()

    # 初始化记忆
    memory = Memory(config=config)
    user_id = "demo_user"

    print("=" * 60)
    print("Powermem 基本使用示例")
    print("=" * 60)

    # 步骤 1: 添加记忆
    print("\n1. 添加记忆...")
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue"
    ]

    for mem in memories:
        memory.add(messages=mem, user_id=user_id, metadata={"source": "demo"})
        print(f"   ✓ 已添加: {mem}")

    # 步骤 2: 搜索记忆
    print("\n2. 搜索记忆...")
    results = memory.search(
        query="user preferences",
        user_id=user_id,
        limit=5
    )

    print(f"   找到 {len(results.get('results', []))} 条记忆:")
    for result in results.get('results', []):
        print(f"     - {result['memory']}")

    # 步骤 3: 获取所有记忆
    print("\n3. 获取所有记忆...")
    all_memories = memory.get_all(user_id=user_id)
    print(f"   总计: {len(all_memories.get('results', []))} 条记忆")

    # 步骤 4: 清理
    print("\n4. 清理...")
    # 删除前获取计数
    all_memories_before = memory.get_all(user_id=user_id)
    count_before = len(all_memories_before.get('results', []))

    # 删除所有记忆 (返回 True/False)
    delete_success = memory.delete_all(user_id=user_id)

    if delete_success:
        print(f"   ✓ 已删除 {count_before} 条记忆")
    else:
        print("   ✗ 删除记忆失败")

    print("\n" + "=" * 60)
    print("示例成功完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
```

**Run this code:**
```bash
python complete_basic_example.py
```

## 扩展练习

### Exercise 1: Multiple Users

Try managing memories for multiple users:

```python
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 为不同用户添加记忆
memory.add("User 1 likes Python", user_id="user1")
memory.add("User 2 likes Java", user_id="user2")

# 搜索每个用户的记忆
results1 = memory.search("preferences", user_id="user1")
results2 = memory.search("preferences", user_id="user2")

memory.add(
    messages="User preference",
    user_id="user123",
    metadata={
        "category": "preference",
        "importance": "high",
        "source": "conversation",
        "timestamp": "2024-01-01",
        "tags": ["python", "programming"]
    }
)

# 按类别搜索
results = memory.search(
    query="programming languages",
    user_id="user123"
)

print(results)

# 使用不同的限制条件进行搜索
results = memory.search(
    query="user information",
    user_id="user123",
    limit=10
)

print(results)
```
```
