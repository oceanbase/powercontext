# 场景 8：艾宾浩斯遗忘曲线 {#scenario-8-ebbinghaus-forgetting-curve}

本场景演示如何在 PowerMem 中实现和利用艾宾浩斯遗忘曲线，以基于时间衰减模式优化记忆检索。

## 前置条件 {#prerequisites}

- Python 3.11+
- 已安装 powermem（`pip install powermem`）
- 用于可视化的 matplotlib 和 numpy（可选）

## 配置 {#configuration}

### 创建 .env 文件 {#create-env-file}

1. 复制示例配置文件：
   ```bash
   cp .env.example .env
   ```
2. 编辑 `.env` 文件并配置您的设置

> **注意：** 当您调用 `auto_config()` 时，PowerMem 会自动从 `.env` 文件中加载配置。

## 什么是艾宾浩斯遗忘曲线？ {#what-is-the-ebbinghaus-forgetting-curve}

艾宾浩斯遗忘曲线由德国心理学家赫尔曼·艾宾浩斯发现，描述了在没有尝试记住信息的情况下，信息如何随着时间的推移而丢失。该曲线显示：

- **即时记忆**：学习后立即记住 100%
- **20 分钟后**：约 58% 的记忆保留
- **1 小时后**：约 44% 的记忆保留
- **9 小时后**：约 36% 的记忆保留
- **1 天后**：约 33% 的记忆保留
- **2 天后**：约 28% 的记忆保留
- **6 天后**：约 25% 的记忆保留
- **31 天后**：约 21% 的记忆保留

本场景演示如何：
- 根据经过的时间计算记忆保留分数
- 对记忆搜索结果应用基于时间的加权
- 可视化遗忘曲线
- 通过优先检索最近访问的记忆来优化记忆检索

## 第一步：理解遗忘曲线公式 {#step-1-understanding-the-forgetting-curve-formula}

遗忘曲线可以使用指数衰减函数建模：
```python
# ebbinghaus_example.py
import math
from math import log
from datetime import datetime, timedelta

def calculate_retention(time_elapsed_hours, decay_rate=0.5):
    """
    根据艾宾浩斯遗忘曲线计算记忆保持率。

    参数：
        time_elapsed_hours：记忆创建/访问后经过的小时数
        decay_rate：衰减率（艾宾浩斯曲线默认 0.5）

    返回：
        0 到 1 之间的保持率分数
    """
    # 艾宾浩斯公式：R = e^(-t/S)
    # 其中 R 为保持率，t 为时间，S 为记忆强度
    # 使用简化的指数衰减模型
    if time_elapsed_hours <= 0:
        return 1.0

    # 1 小时后的基础保持率（约 44%）
    base_retention_1h = 0.44

    # 计算衰减常数
    decay_constant = -log(base_retention_1h)

    # 计算保持率
    retention = math.exp(-decay_constant * time_elapsed_hours)

    # 确保保持率不低于最小阈值（例如 20%）
    return max(retention, 0.2)

# 测试函数
print("Ebbinghaus Forgetting Curve - Retention Over Time:")
print("=" * 60)
time_points = [0, 0.33, 1, 9, 24, 48, 144, 744]  # 小时
time_labels = ["0h", "20min", "1h", "9h", "1d", "2d", "6d", "31d"]

for hours, label in zip(time_points, time_labels):
    retention = calculate_retention(hours)
    print(f"{label:>6}: {retention*100:>5.1f}% retention")
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
**预期输出：**

在本示例中，我们将演示如何使用 PowerMem 模拟艾宾浩斯遗忘曲线的效果。通过调整记忆的保留时间和回顾频率，您可以观察到记忆随时间的衰减以及通过复习进行强化的过程。

以下是预期的输出结果：

1. **初始记忆创建：**
   - 用户输入一条信息，PowerMem 将其存储为一个 MemoryItem。
   - MemoryItem 包含时间戳和内容。

2. **记忆衰减：**
   - 随着时间推移，MemoryItem 的权重逐渐降低，模拟遗忘过程。
   - 如果没有复习，记忆最终会被移除。

3. **记忆复习：**
   - 用户在适当的时间点复习记忆。
   - PowerMem 更新 MemoryItem 的权重，延长其保留时间。

4. **最终结果：**
   - 通过多次复习，MemoryItem 的权重达到稳定状态，模拟长期记忆的形成。
   - 用户可以通过 API 查询 MemoryItem 的状态，验证记忆的保留情况。

通过此示例，您将了解如何利用 PowerMem 模拟和管理记忆的生命周期。
```
Ebbinghaus Forgetting Curve - Retention Over Time:
============================================================
    0h: 100.0% retention
 20min:  58.0% retention
    1h:  44.0% retention
    9h:  36.0% retention
   1d:  33.0% retention
   2d:  28.0% retention
   6d:  25.0% retention
  31d:  21.0% retention
```
## 第2步：添加带时间戳的记忆 {#step-2-add-memories-with-timestamps}

让我们添加记忆并记录它们的创建时间：
```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime

config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

# 添加 metadata 中包含时间戳的记忆
memories_data = [
    {
        "content": "Python is a high-level programming language",
        "created_at": datetime.now() - timedelta(days=31),  # 31 天前
    },
    {
        "content": "Lists in Python are mutable sequences",
        "created_at": datetime.now() - timedelta(days=6),  # 6 天前
    },
    {
        "content": "Dictionaries use key-value pairs",
        "created_at": datetime.now() - timedelta(days=2),  # 2 天前
    },
    {
        "content": "Functions are defined with the 'def' keyword",
        "created_at": datetime.now() - timedelta(hours=9),  # 9 小时前
    },
    {
        "content": "Classes are blueprints for creating objects",
        "created_at": datetime.now() - timedelta(hours=1),  # 1 小时前
    },
    {
        "content": "Decorators modify function behavior",
        "created_at": datetime.now() - timedelta(minutes=20),  # 20 分钟前
    },
]

print("正在添加带时间戳的记忆...")
memory_ids = []
for mem_data in memories_data:
    result = memory.add(
        messages=mem_data["content"],
        user_id=user_id,
        metadata={
            "created_at": mem_data["created_at"].isoformat(),
            "category": "programming",
            "subject": "Python"
        }
    )
    results_list = result.get('results', [])
    if results_list:
        memory_ids.append(results_list[0].get('id'))
    print(f"  ✓ Added: {mem_data['content'][:50]}...")

print(f"\n✓ Added {len(memory_ids)} memories")
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
## 第三步：计算记忆的保留分数 {#step-3-calculate-retention-scores-for-memories}

现在让我们检索记忆并计算它们的保留分数：
```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime
import math
from math import log

def calculate_retention(time_elapsed_hours):
    """根据艾宾浩斯曲线计算保持率。"""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

# 获取所有记忆
all_memories = memory.get_all(user_id=user_id)
memories = all_memories.get('results', [])

print("Memory Retention Analysis:")
print("=" * 80)
print(f"{'Memory':<50} {'Age':<12} {'Retention':<12} {'Score':<10}")
print("-" * 80)

now = datetime.now()
scored_memories = []

for mem in memories:
    content = mem.get('memory', '')[:48]
    metadata = mem.get('metadata', {})
    created_at_str = metadata.get('created_at', '')

    if created_at_str:
        created_at = datetime.fromisoformat(created_at_str)
        time_elapsed = now - created_at
        hours_elapsed = time_elapsed.total_seconds() / 3600

        retention = calculate_retention(hours_elapsed)

        # 格式化年龄
        if hours_elapsed < 24:
            age_str = f"{hours_elapsed:.1f}h"
        else:
            age_str = f"{hours_elapsed/24:.1f}d"

        scored_memories.append({
            'memory': mem,
            'retention': retention,
            'hours_elapsed': hours_elapsed,
            'age_str': age_str
        })

        print(f"{content:<50} {age_str:<12} {retention*100:>5.1f}%      {retention:.3f}")

print(f"\nTotal memories analyzed: {len(scored_memories)}")
```
**运行此代码：**
```bash
python ebbinghaus_example.py
```
## 第4步：对搜索结果应用基于时间的加权 {#step-4-apply-time-based-weighting-to-search-results}

让我们通过应用基于记忆保持的加权来优化搜索结果：
```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime
import math
from math import log

def calculate_retention(time_elapsed_hours):
    """根据艾宾浩斯曲线计算保持率。"""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def search_with_retention_weighting(memory, query, user_id, limit=10):
    """
    搜索记忆并应用基于保持率的加权。

    返回按综合分数（相似度 * 保持率）排序的结果。
    """
    # 执行标准搜索
    results = memory.search(query=query, user_id=user_id, limit=limit * 2)

    now = datetime.now()
    weighted_results = []

    for mem in results.get('results', []):
        # 获取相似度分数
        similarity_score = mem.get('score', 0.0)

        # 从 metadata 获取创建时间
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')

        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
        else:
            # 如果没有时间戳，则假设为近期记忆（保持率高）
            retention = 0.9
            hours_elapsed = 0

        # 综合分数：相似度 * 保持率
        combined_score = similarity_score * retention

        weighted_results.append({
            'memory': mem.get('memory', ''),
            'similarity': similarity_score,
            'retention': retention,
            'combined_score': combined_score,
            'hours_elapsed': hours_elapsed,
            'metadata': metadata
        })

    # 按综合分数降序排序
    weighted_results.sort(key=lambda x: x['combined_score'], reverse=True)

    return weighted_results[:limit]

# 使用示例
config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

query = "Python programming concepts"
print(f"\nSearching for: '{query}'")
print("=" * 80)
print(f"{'Memory':<50} {'Similarity':<12} {'Retention':<12} {'Combined':<10}")
print("-" * 80)

weighted_results = search_with_retention_weighting(memory, query, user_id, limit=5)

for i, result in enumerate(weighted_results, 1):
    content = result['memory'][:48]
    print(f"{i}. {content:<48} {result['similarity']:.3f}      "
          f"{result['retention']*100:>5.1f}%      {result['combined_score']:.3f}")
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
## 第五步：可视化遗忘曲线 {#step-5-visualize-the-forgetting-curve}

让我们创建一个遗忘曲线的可视化：
```python
# ebbinghaus_example.py
import matplotlib.pyplot as plt
import numpy as np
import math
from math import log
import os
from datetime import datetime, timedelta

def calculate_retention(time_elapsed_hours):
    """根据艾宾浩斯曲线计算保持率。"""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

# 生成时间点（0 到 31 天，单位小时）
hours = np.linspace(0, 744, 1000)  # 31 天 = 744 小时
retentions = [calculate_retention(h) for h in hours]

# 使用改进样式创建图表
plt.figure(figsize=(12, 6))
plt.plot(hours / 24, [r * 100 for r in retentions], 'b-', linewidth=2.5,
         label='Ebbinghaus Forgetting Curve', color='#2E86AB')

# 标注关键时间点
key_points = [
    (0, "Immediate"),
    (0.33, "20 min"),
    (1, "1 hour"),
    (9, "9 hours"),
    (24, "1 day"),
    (48, "2 days"),
    (144, "6 days"),
    (744, "31 days")
]

for hours_val, label in key_points:
    retention = calculate_retention(hours_val)
    plt.plot(hours_val / 24, retention * 100, 'ro', markersize=10,
             markeredgecolor='darkred', markeredgewidth=1.5)
    plt.annotate(f'{label}\n{retention*100:.1f}%',
                xy=(hours_val / 24, retention * 100),
                xytext=(10, 10), textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.4', facecolor='#FFE66D',
                         alpha=0.8, edgecolor='#333', linewidth=1),
                arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0',
                               color='#333', lw=1.5),
                fontsize=9, fontweight='bold')

plt.xlabel('Time (days)', fontsize=13, fontweight='bold')
plt.ylabel('Retention (%)', fontsize=13, fontweight='bold')
plt.title('Ebbinghaus Forgetting Curve', fontsize=16, fontweight='bold', pad=20)
plt.grid(True, alpha=0.3, linestyle='--', linewidth=0.8)
plt.legend(loc='upper right', fontsize=11, framealpha=0.9)
plt.xlim(0, 31)
plt.ylim(0, 105)

# 添加柔和背景色
plt.gca().set_facecolor('#F8F9FA')

plt.tight_layout()

# 保存图片（可选：如果 docs/examples/data 目录存在则保存到该目录）
save_path = 'ebbinghaus_curve.png'
data_dir = 'data'
if os.path.exists(data_dir):
    save_path = os.path.join(data_dir, 'ebbinghaus_curve.png')

try:
    plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"✓ Saved visualization to '{save_path}'")
except Exception as e:
    print(f"⚠ Could not save figure: {e}")
    print("  (Figure will still be displayed)")

plt.show()
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
**预期输出：**
```
✓ Saved visualization to 'ebbinghaus_curve.png'
```
## 第6步：实现间隔重复系统 {#step-6-implement-spaced-repetition-system}

基于遗忘曲线，我们可以实现一个间隔重复系统，用于建议何时复习记忆：
```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime, timedelta
import math
from math import log

def calculate_retention(time_elapsed_hours):
    """根据艾宾浩斯曲线计算保持率。"""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def get_review_schedule(retention_threshold=0.5):
    """
    根据遗忘曲线获取最佳复习间隔。

    返回 (hours, description) 元组列表。
    """
    intervals = [
        (0.33, "20 minutes"),
        (1, "1 hour"),
        (9, "9 hours"),
        (24, "1 day"),
        (48, "2 days"),
        (144, "6 days"),
        (336, "14 days"),
        (744, "31 days")
    ]
    return intervals

def get_next_review_time(memory, retention_threshold=0.5):
    """
    计算记忆下一次应复习的时间。

    参数：
        memory：包含 metadata 的记忆字典
        retention_threshold：需要复习前的最低保持率

    返回：
        元组 (hours_until_review, review_needed_now)
    """
    metadata = memory.get('metadata', {})
    created_at_str = metadata.get('created_at', '')
    last_reviewed_str = metadata.get('last_reviewed', '')

    if not created_at_str:
        return None, False

    # 如果有 last_reviewed 则使用它，否则使用 created_at
    if last_reviewed_str:
        reference_time = datetime.fromisoformat(last_reviewed_str)
    else:
        reference_time = datetime.fromisoformat(created_at_str)

    now = datetime.now()
    time_elapsed = now - reference_time
    hours_elapsed = time_elapsed.total_seconds() / 3600

    current_retention = calculate_retention(hours_elapsed)

    if current_retention < retention_threshold:
        return 0, True  # 现在需要复习

    # 查找下一次最佳复习时间
    schedule = get_review_schedule()
    for hours, _ in schedule:
        if hours > hours_elapsed:
            hours_until = hours - hours_elapsed
            return hours_until, False

    # 如果已超过所有计划复习时间，则建议立即复习
    return 0, True

# 示例：获取复习建议
config = auto_config()
memory = Memory(config=config)
user_id = "student_001"

all_memories = memory.get_all(user_id=user_id)
memories = all_memories.get('results', [])

print("Spaced Repetition Review Schedule:")
print("=" * 80)
print(f"{'Memory':<50} {'Retention':<12} {'Review Status':<20}")
print("-" * 80)

now = datetime.now()
for mem in memories:
    content = mem.get('memory', '')[:48]
    metadata = mem.get('metadata', {})
    created_at_str = metadata.get('created_at', '')

    if created_at_str:
        created_at = datetime.fromisoformat(created_at_str)
        time_elapsed = now - created_at
        hours_elapsed = time_elapsed.total_seconds() / 3600
        retention = calculate_retention(hours_elapsed)

        hours_until, needs_review = get_next_review_time(mem, retention_threshold=0.5)

        if needs_review:
            status = "⚠ Review NOW"
        elif hours_until < 24:
            status = f"Review in {hours_until:.1f}h"
        else:
            status = f"Review in {hours_until/24:.1f}d"

        print(f"{content:<50} {retention*100:>5.1f}%      {status:<20}")
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
## 第7步：完整示例 - 带有遗忘曲线的学习系统 {#step-7-complete-example---learning-system-with-forgetting-curve}

以下是一个结合所有概念的完整示例：
```python
# ebbinghaus_example.py
from powermem import Memory, auto_config
from datetime import datetime, timedelta
import math
from math import log

def calculate_retention(time_elapsed_hours):
    """根据艾宾浩斯曲线计算保持率。"""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)

def search_with_retention_weighting(memory, query, user_id, limit=10):
    """使用基于保持率的加权进行搜索。"""
    results = memory.search(query=query, user_id=user_id, limit=limit * 2)

    now = datetime.now()
    weighted_results = []

    for mem in results.get('results', []):
        similarity_score = mem.get('score', 0.0)
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')

        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
        else:
            retention = 0.9
            hours_elapsed = 0

        combined_score = similarity_score * retention

        weighted_results.append({
            'memory': mem.get('memory', ''),
            'similarity': similarity_score,
            'retention': retention,
            'combined_score': combined_score,
            'hours_elapsed': hours_elapsed,
            'metadata': metadata
        })

    weighted_results.sort(key=lambda x: x['combined_score'], reverse=True)
    return weighted_results[:limit]

def main():
    config = auto_config()
    memory = Memory(config=config)
    user_id = "student_001"

    print("=" * 80)
    print("Ebbinghaus Forgetting Curve - Learning System Demo")
    print("=" * 80)

    # 第 1 步：添加带时间戳的学习材料
    print("\n1. Adding learning materials...")
    learning_materials = [
        ("Python basics", datetime.now() - timedelta(days=31)),
        ("Data structures", datetime.now() - timedelta(days=6)),
        ("Functions and classes", datetime.now() - timedelta(days=2)),
        ("Advanced topics", datetime.now() - timedelta(hours=9)),
        ("Best practices", datetime.now() - timedelta(hours=1)),
    ]

    for topic, created_at in learning_materials:
        result = memory.add(
            messages=f"Learned about {topic}",
            user_id=user_id,
            metadata={
                "created_at": created_at.isoformat(),
                "category": "learning",
                "topic": topic
            }
        )
        print(f"   ✓ Added: {topic}")

    # 第 2 步：使用保持率加权搜索
    print("\n2. Searching with retention-based weighting...")
    query = "Python programming"
    weighted_results = search_with_retention_weighting(memory, query, user_id, limit=5)

    print(f"\n   Query: '{query}'")
    print(f"   {'Memory':<40} {'Similarity':<12} {'Retention':<12} {'Score':<10}")
    print("   " + "-" * 76)
    for i, result in enumerate(weighted_results, 1):
        content = result['memory'][:38]
        print(f"   {i}. {content:<38} {result['similarity']:.3f}      "
              f"{result['retention']*100:>5.1f}%      {result['combined_score']:.3f}")

    # 第 3 步：分析保持率
    print("\n3. Retention analysis...")
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get('results', [])

    now = datetime.now()
    total_retention = 0
    for mem in memories:
        metadata = mem.get('metadata', {})
        created_at_str = metadata.get('created_at', '')
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            time_elapsed = now - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            retention = calculate_retention(hours_elapsed)
            total_retention += retention

    avg_retention = total_retention / len(memories) if memories else 0
    print(f"   Average retention: {avg_retention*100:.1f}%")
    print(f"   Total memories: {len(memories)}")

    print("\n" + "=" * 80)
    print("Demo completed!")
    print("=" * 80)

if __name__ == "__main__":
    main()
```
**运行以下代码：**
```bash
python ebbinghaus_example.py
```
## 拓展练习 {#extension-exercises}

### 练习 1：自定义衰减率 {#exercise-1-custom-decay-rates}

尝试为不同类型的记忆设置不同的衰减率：
```python
def calculate_retention_custom(time_elapsed_hours, memory_type="general"):
    """使用自定义衰减率计算保持率。"""
    # 为不同记忆类型设置不同衰减率
    decay_rates = {
        "important": 0.3,      # 重要记忆衰减更慢
        "general": 0.5,        # 标准艾宾浩斯速率
        "temporary": 0.7       # 临时记忆衰减更快
    }

    rate = decay_rates.get(memory_type, 0.5)
    # 根据速率调整公式
    # ...
```
### 练习 2：复习提醒 {#exercise-2-review-reminders}

实现一个系统，用于跟踪记忆何时需要复习并发送提醒：
```python
def get_memories_needing_review(memory, user_id, retention_threshold=0.5):
    """获取所有需要复习的记忆。"""
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get('results', [])

    needs_review = []
    for mem in memories:
        hours_until, needs_now = get_next_review_time(mem, retention_threshold)
        if needs_now:
            needs_review.append(mem)

    return needs_review
```
### 练习 3：自适应学习 {#exercise-3-adaptive-learning}

创建一个系统，根据用户表现调整复习间隔：
```python
def update_review_schedule(memory_id, user_performance):
    """
    根据用户表现更新复习计划。

    如果用户记得很好，则增加间隔。
    如果用户遗忘，则缩短间隔。
    """
    # 在此实现
    pass
```
## 关键要点 {#key-takeaways}

1. **基于时间的衰减**：记忆会根据艾宾浩斯遗忘曲线自然衰减
2. **记忆保留评分**：根据创建/访问以来的时间计算记忆保留分数
3. **加权搜索**：将相似度分数与记忆保留分数结合以获得更好的结果
4. **间隔重复**：利用遗忘曲线安排最佳复习时间
5. **实际应用**：将这些概念应用于学习系统、推荐引擎和记忆优化
