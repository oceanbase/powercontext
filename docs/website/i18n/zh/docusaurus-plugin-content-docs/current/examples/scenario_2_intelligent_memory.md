# 场景 2：智能记忆 {#scenario-2-intelligent-memory}

本场景展示了 PowerMem 的智能记忆功能——自动事实提取、重复检测、冲突解决等。

## 前置条件 {#prerequisites}

- 完成场景 1
- 已安装 PowerMem
- 已配置 LLM 提供商（用于智能功能）

## 理解智能记忆 {#understanding-intelligent-memory}

智能记忆使 PowerMem 能够：
- 自动从对话中提取事实
- 检测并防止重复
- 在信息发生变化时更新现有记忆
- 解决矛盾信息之间的冲突
- 整合相关记忆

## 第一步：启用智能处理 {#step-1-enable-intelligent-processing}

首先，让我们看看如何启用智能处理：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 使用智能处理添加记忆（infer=True）
result = memory.add(
    messages=[
        {"role": "user", "content": "Hi, my name is Alice. I'm a software engineer at Google."},
        {"role": "assistant", "content": "Nice to meet you, Alice! That's interesting."},
        {"role": "user", "content": "I love Python programming and machine learning."}
    ],
    user_id="user_001",
    infer=True  # 启用智能事实提取
)

print(f"✓ Processed conversation, extracted {len(result.get('results', []))} memories:")
for i, mem in enumerate(result.get('results', []), 1):
    print(f"  {i}. {mem.get('memory', '')}")
```
**运行以下代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
✓ Processed conversation, extracted 3 memories:
  1. Name is Alice
  2. Is a software engineer at Google
  3. Loves Python programming and machine learning
```
## 第2步：重复检测 {#step-2-duplicate-detection}

智能记忆会自动检测重复项：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user_001"

# 首次添加
print("1. Adding initial memory...")
result1 = memory.add(
    messages=[
        {"role": "user", "content": "I'm Alice, a software engineer"},
        {"role": "assistant", "content": "I remember that!"}
    ],
    user_id=user_id,
    infer=True
)
print(f"   Added {len(result1.get('results', []))} memories")

# 尝试添加重复记忆
print("\n2. Attempting to add duplicate...")
result2 = memory.add(
    messages=[
        {"role": "user", "content": "I'm Alice, a software engineer"},
        {"role": "assistant", "content": "I know!"}
    ],
    user_id=user_id,
    infer=True
)

results = result2.get('results', [])
if results:
    event = results[0].get('event', 'N/A')
    print(f"   Event: {event}")
    if event == 'NONE':
        print("   ✓ Duplicate detected, no new memory created")
else:
    print("   ✓ Duplicate detected, skipped")
```
**运行以下代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
1. Adding initial memory...
   Added 2 memories

2. Attempting to add duplicate...
   Event: NONE
   ✓ Duplicate detected, no new memory created
```
## 第三步：信息更新 {#step-3-information-updates}

当信息发生变化时，记忆会自动更新：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user_001"

# 初始信息
print("1. Adding initial information...")
memory.add(
    messages=[
        {"role": "user", "content": "I work at Google as a software engineer"}
    ],
    user_id=user_id,
    infer=True
)

# 信息更新
print("\n2. Updating information...")
result = memory.add(
    messages=[
        {"role": "user", "content": "I recently moved to Meta as a senior ML engineer"}
    ],
    user_id=user_id,
    infer=True
)

print("\n3. Checking results...")
for mem in result.get('results', []):
    event = mem.get('event', 'N/A')
    print(f"   Event: {event}")
    if event == 'UPDATE':
        print(f"   Updated memory:")
        print(f"     Old: {mem.get('previous_memory', 'N/A')}")
        print(f"     New: {mem.get('memory', 'N/A')}")
```
**运行以下代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
1. Adding initial information...
2. Updating information...
3. Checking results...
   Event: UPDATE
   Updated memory:
     Old: Works at Google as a software engineer
     New: Works at Meta as a senior ML engineer
```
## 第4步：添加新信息 {#step-4-adding-new-information}

新的、非冲突性的信息会正常添加：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user_001"

# 添加已有记忆
memory.add(
    messages=[
        {"role": "user", "content": "I'm Alice, a software engineer"}
    ],
    user_id=user_id,
    infer=True
)

# 添加新信息
print("Adding new information...")
result = memory.add(
    messages=[
        {"role": "user", "content": "I like to drink coffee every morning and I have two cats."},
        {"role": "assistant", "content": "That's nice! What are your cats' names?"},
        {"role": "user", "content": "Their names are Fluffy and Whiskers."}
    ],
    user_id=user_id,
    infer=True
)

print(f"\n✓ Added {len(result.get('results', []))} new memories:")
for mem in result.get('results', []):
    event = mem.get('event', 'N/A')
    print(f"  [{event}] {mem.get('memory', '')}")
```
**运行以下代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
Adding new information...

✓ Added 3 new memories:
  [ADD] Likes to drink coffee every morning
  [ADD] Has two cats
  [ADD] Cats named Fluffy and Whiskers
```
## 第五步：冲突解决 {#step-5-conflict-resolution}

当检测到矛盾信息时，系统会解决冲突：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user_001"

# 添加初始偏好
print("1. Adding initial preference...")
memory.add(
    messages=[
        {"role": "user", "content": "I like to drink coffee every morning"}
    ],
    user_id=user_id,
    infer=True
)

# 矛盾信息
print("\n2. Adding contradictory information...")
result = memory.add(
    messages=[
        {"role": "user", "content": "Actually, I don't like coffee anymore. I prefer tea now."}
    ],
    user_id=user_id,
    infer=True
)

print("\n3. Conflict resolution results:")
for mem in result.get('results', []):
    event = mem.get('event', 'N/A')
    print(f"   Event: {event}")
    if event == 'DELETE':
        print(f"     Deleted: {mem.get('memory', 'N/A')}")
    elif event == 'ADD':
        print(f"     Added: {mem.get('memory', 'N/A')}")
```
**运行此代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
1. Adding initial preference...
2. Adding contradictory information...
3. Conflict resolution results:
   Event: DELETE
     Deleted: Likes to drink coffee every morning
   Event: ADD
     Added: Prefers tea instead of coffee
```
## 第六步：记忆整合 {#step-6-memory-consolidation}

相关记忆被整合：
```python
# intelligent_memory_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user_001"

# 初始记忆
print("1. Adding initial memory...")
memory.add(
    messages=[
        {"role": "user", "content": "I love Python programming"}
    ],
    user_id=user_id,
    infer=True
)

# 更详细的信息
print("\n2. Adding more detailed information...")
result = memory.add(
    messages=[
        {"role": "user", "content": "I love Python, especially for deep learning. I use TensorFlow and PyTorch a lot."}
    ],
    user_id=user_id,
    infer=True
)

print("\n3. Consolidation results:")
for mem in result.get('results', []):
    event = mem.get('event', 'N/A')
    if event == 'UPDATE':
        print(f"   Updated memory:")
        print(f"     Old: {mem.get('previous_memory', 'N/A')}")
        print(f"     New: {mem.get('memory', 'N/A')}")
    else:
        print(f"   [{event}] {mem.get('memory', '')}")
```
**运行以下代码：**
```bash
python intelligent_memory_example.py
```
**预期输出：**
```
1. Adding initial memory...
2. Adding more detailed information...
3. Consolidation results:
   Updated memory:
     Old: Loves Python programming
     New: Loves Python programming, especially for deep learning using TensorFlow and PyTorch
```
## 完整示例 {#complete-example}

以下是一个展示所有智能功能的完整示例：
```python
# complete_intelligent_example.py
from powermem import Memory, auto_config

def main():
    config = auto_config()
    memory = Memory(config=config)
    user_id = "demo_user"

    print("=" * 80)
    print("Intelligent Memory Demo")
    print("=" * 80)

    # 场景 1：初始添加
    print("\n[Scenario 1] Initial Memory Addition")
    print("-" * 60)
    result = memory.add(
        messages=[
            {"role": "user", "content": "Hi, my name is Alice. I'm a software engineer at Google."},
            {"role": "assistant", "content": "Nice to meet you, Alice!"},
            {"role": "user", "content": "I love Python programming and machine learning."}
        ],
        user_id=user_id,
        infer=True
    )
    print(f"✓ Extracted {len(result.get('results', []))} memories")

    # 场景 2：重复检测
    print("\n[Scenario 2] Duplicate Detection")
    print("-" * 60)
    result = memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"}
        ],
        user_id=user_id,
        infer=True
    )
    if result.get('results', []):
        if result['results'][0].get('event') == 'NONE':
            print("✓ Duplicate detected, skipped")

    # 场景 3：信息更新
    print("\n[Scenario 3] Information Update")
    print("-" * 60)
    result = memory.add(
        messages=[
            {"role": "user", "content": "I recently moved to Meta as a senior ML engineer"}
        ],
        user_id=user_id,
        infer=True
    )
    for mem in result.get('results', []):
        if mem.get('event') == 'UPDATE':
            print(f"✓ Updated: {mem.get('previous_memory')} → {mem.get('memory')}")

    # 场景 4：新信息
    print("\n[Scenario 4] Adding New Information")
    print("-" * 60)
    result = memory.add(
        messages=[
            {"role": "user", "content": "I like to drink coffee every morning and I have two cats."}
        ],
        user_id=user_id,
        infer=True
    )
    print(f"✓ Added {len(result.get('results', []))} new memories")

    # 场景 5：冲突解决
    print("\n[Scenario 5] Conflict Resolution")
    print("-" * 60)
    result = memory.add(
        messages=[
            {"role": "user", "content": "Actually, I prefer tea instead of coffee now."}
        ],
        user_id=user_id,
        infer=True
    )
    for mem in result.get('results', []):
        event = mem.get('event', '')
        if event == 'DELETE':
            print(f"✓ Deleted conflicting memory")
        elif event == 'ADD':
            print(f"✓ Added new preference")

    # 最终总结
    print("\n" + "=" * 80)
    print("Final Memory Summary")
    print("=" * 80)
    all_memories = memory.get_all(user_id=user_id)
    print(f"\nTotal memories: {len(all_memories.get('results', []))}")
    for i, mem in enumerate(all_memories.get('results', []), 1):
        print(f"  {i}. {mem.get('memory', '')}")

    print("\n" + "=" * 80)
    print("Demo completed successfully!")
    print("=" * 80)

if __name__ == "__main__":
    main()
```
**运行以下代码：**
```bash
python complete_intelligent_example.py
```
## 拓展练习 {#extension-exercises}

### 练习 1：比较简单模式与智能模式 {#exercise-1-compare-simple-vs-intelligent-mode}

比较在有智能处理和无智能处理情况下添加记忆：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 简单模式
print("1. Simple mode (infer=False):")
result1 = memory.add("User likes Python", user_id="user123", infer=False)
print(f"   Added memory directly: {result1.get('results', [{}])[0].get('memory', 'N/A')}")

# 智能模式
print("\n2. Intelligent mode (infer=True):")
result2 = memory.add(
    messages=[{"role": "user", "content": "I like Python programming"}],
    user_id="user123",
    infer=True
)

print("   Extracted memories:")
for mem in result2.get('results', []):
    event = mem.get('event', 'N/A')
    memory_text = mem.get('memory', '')
    print(f"   - [{event}] {memory_text}")

print("\n✓ Comparison completed. Intelligent mode extracts facts automatically!")
```
### 练习 2：跟踪记忆事件 {#exercise-2-track-memory-events}

监控不同的记忆操作：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 示例：添加记忆并检查事件类型
result = memory.add(
    messages=[
        {"role": "user", "content": "I love working with machine learning"}
    ],
    user_id="user123",
    infer=True
)

print("Processing results:")
for mem in result.get('results', []):
    event = mem.get('event')
    if event == 'ADD':
        print(f"✓ New memory added: {mem.get('memory', '')}")
    elif event == 'UPDATE':
        print(f"✓ Memory updated: {mem.get('previous_memory', '')} → {mem.get('memory', '')}")
    elif event == 'DELETE':
        print(f"✓ Memory deleted: {mem.get('memory', '')}")
    elif event == 'NONE':
        print("✓ Duplicate detected, skipped")
```
### 练习 3：复杂对话 {#exercise-3-complex-conversations}

处理更长的对话：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 包含多个事实的长对话
long_conversation = [
    {"role": "user", "content": "I'm Alice, a software engineer at Google."},
    {"role": "assistant", "content": "Nice to meet you!"},
    {"role": "user", "content": "I work on machine learning projects."},
    {"role": "assistant", "content": "That's interesting!"},
    {"role": "user", "content": "I use Python, TensorFlow, and PyTorch."},
]

print("Processing long conversation...")
result = memory.add(
    messages=long_conversation,
    user_id="user123",
    infer=True
)

print(f"\n✓ Extracted {len(result.get('results', []))} memories:")
for i, mem in enumerate(result.get('results', []), 1):
    event = mem.get('event', 'N/A')
    memory_text = mem.get('memory', '')
    print(f"  {i}. [{event}] {memory_text}")
```
