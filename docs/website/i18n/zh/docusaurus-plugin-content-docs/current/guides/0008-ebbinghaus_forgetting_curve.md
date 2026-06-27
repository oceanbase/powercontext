---
title: 艾宾浩斯遗忘曲线指南
sidebar_label: 艾宾浩斯遗忘曲线
---

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

> **注意：** 当您调用 `Memory()` 时，powermem 会自动从 `.env` 文件中加载配置。

### 使用 JSON/字典配置 {#using-jsondictionary-configuration}

除了使用 `.env` 文件，您还可以直接以 Python 字典（类似 JSON 的格式）传递配置。这在以下情况下非常有用：
- 您希望从 JSON 文件加载配置
- 您需要以编程方式生成配置
- 您将配置嵌入到您的应用程序代码中

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
## 什么是艾宾浩斯遗忘曲线？ {#what-is-the-ebbinghaus-forgetting-curve}

艾宾浩斯遗忘曲线由德国心理学家赫尔曼·艾宾浩斯在19世纪80年代发现，描述了在没有尝试保留信息的情况下，信息如何随着时间的推移而丢失。这一认知心理学的基本原理对AI应用中的记忆系统具有深远的影响。

### 关键洞察 {#key-insights}

遗忘曲线揭示了记忆保留遵循可预测的指数衰减模式：

- **即时保留**：学习后立即保留100%
- **20分钟后**：约58%保留
- **1小时后**：约44%保留
- **9小时后**：约36%保留
- **1天后**：约33%保留
- **2天后**：约28%保留
- **6天后**：约25%保留
- **31天后**：约21%保留

### 为什么这对AI记忆系统很重要 {#why-this-matters-for-ai-memory-systems}

在传统记忆系统中，所有记忆被平等对待，无论它们何时创建。然而，遗忘曲线表明：

1. **最近性很重要**：最近创建或访问的记忆更可能是相关的
2. **基于时间的权重**：较旧的记忆在搜索结果中的优先级应降低
3. **间隔重复**：应在最佳间隔复习记忆以维持保留
4. **自动衰减**：如果没有干预，记忆的相关性会随着时间自然降低

### 使用场景 {#use-cases}

在PowerMem中实施遗忘曲线原理可以实现：

- **智能搜索排序**：优先考虑更可能相关的最近记忆
- **记忆新鲜度跟踪**：识别可能需要更新的陈旧记忆
- **间隔重复系统**：构建优化保留的学习和复习系统
- **上下文感知检索**：基于语义相似性和时间相关性加权搜索结果

## 指南概览 {#guide-overview}

本指南将引导您完成以下内容：

1. **理解公式**：学习如何数学建模遗忘曲线
2. **跟踪记忆时间戳**：存储和检索创建/访问时间
3. **计算保留分数**：评估每个记忆的“新鲜度”
4. **加权搜索**：结合语义相似性和时间相关性
5. **可视化**：创建图表以理解记忆衰减模式
6. **间隔重复**：基于保留阈值实施复习计划

## 理解遗忘曲线公式 {#understanding-the-forgetting-curve-formula}

### 数学基础 {#mathematical-foundation}

遗忘曲线可以用指数衰减函数进行数学建模。核心原理是记忆保留随时间呈指数下降，公式如下：

**R(t) = e^(-λt)**

其中：
- **R(t)** 是时间t时的保留率
- **λ**（lambda）是衰减常数
- **t** 是自学习以来经过的时间

### 实现方法 {#implementation-approach}

在我们的实现中，我们基于艾宾浩斯的经验观察使用了一个简化模型：
- 我们校准衰减常数以匹配1小时后44%的保留率
- 我们设置了最低保留阈值（20%），以防止记忆完全失去相关性
- 该公式确保了与人类记忆模式相符的平滑衰减

### 为什么这个公式有效 {#why-this-formula-works}

指数衰减模型捕捉了几个重要特性：

1. **快速初始衰减**：大部分遗忘发生在最初几小时内
2. **逐渐的长期衰减**：在初始下降后，保留率趋于稳定
3. **可预测的模式**：曲线允许我们计算任意时间点的保留率
4. **实用的阈值**：最低保留确保旧记忆不会被完全丢弃

### 自定义选项 {#customization-options}

您可以调整公式参数以匹配您的具体使用场景：

- **衰减速率**：根据记忆重要性调整遗忘速度
- **最低保留**：为非常旧的记忆赋予多少权重
- **基础保留**：校准到不同的时间点（例如1小时、1天）

以下是实现代码：
```python
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
## 添加带时间戳的记忆 {#add-memories-with-timestamps}

### 为什么要跟踪时间戳？ {#why-track-timestamps}

为了应用遗忘曲线，我们需要知道每个记忆的创建时间或上次访问时间。这些时间信息可以帮助我们：

- 计算自记忆创建以来经过的时间
- 确定每个记忆的当前保留分数
- 根据最近性优先处理记忆
- 为间隔重复安排复习时间

### 实现策略 {#implementation-strategy}

powermem 的 `metadata` 字段非常适合存储时间戳信息。我们将：

1. 将创建时间以 ISO 格式的字符串存储在 metadata 中
2. 包含额外的上下文（类别、主题）以便更好地组织
3. 使用时间戳在后续计算保留分数

### 最佳实践 {#best-practices}

在为记忆添加时间戳时：

- **使用 ISO 格式**：将时间戳存储为 ISO 8601 字符串以便于解析（`datetime.isoformat()`）
- **存储创建时间**：始终记录记忆首次创建的时间
- **跟踪访问时间**：可选地在记忆被检索时更新 `last_accessed`
- **包含上下文**：添加类别、标签或重要性等 metadata，以便更丰富的筛选

### 示例实现 {#example-implementation}

让我们添加记忆并跟踪它们的创建时间：
```python
from powermem import Memory, auto_config
from datetime import datetime, timedelta

memory = Memory(config=auto_config())
user_id = "student_001"

# 添加带 metadata 时间戳的记忆
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
## 计算记忆的保留分数 {#calculate-retention-scores-for-memories}

### 什么是保留分数？ {#what-are-retention-scores}

保留分数量化了基于遗忘曲线的记忆“新鲜度”或相关性。它是一个介于 0 和 1 之间的值，其中：

- **1.0**：记忆刚刚创建（100% 保留）
- **0.44**：记忆已有 1 小时（44% 保留，根据艾宾浩斯遗忘曲线）
- **0.2**：记忆非常久远（最低阈值）

### 如何使用保留分数 {#how-to-use-retention-scores}

保留分数可以实现以下强大功能：

1. **记忆新鲜度分析**：识别哪些记忆已经过时，可能需要更新
2. **搜索结果排序**：在搜索结果中提升最近的记忆
3. **复习计划安排**：确定何时应该复习记忆
4. **记忆清理**：识别已衰减到无用阈值以下的记忆

### 实现工作流程 {#implementation-workflow}

该过程包括以下步骤：

1. **检索记忆**：获取用户的所有记忆
2. **提取时间戳**：从元数据中解析创建时间
3. **计算经过时间**：计算自创建以来的小时/天数
4. **应用公式**：使用遗忘曲线公式计算保留分数
5. **显示结果**：以可读格式显示记忆的年龄和保留分数

### 结果解读 {#interpreting-results}

在分析保留分数时：

- **高保留（`>0.7`）**：非常新的记忆，可能高度相关
- **中等保留（0.3-0.7）**：适度陈旧，可能需要尽快复习
- **低保留（`<0.3`）**：旧记忆，考虑更新或复习

现在让我们检索记忆并计算它们的保留分数：
```python
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

memory = Memory(config=auto_config())
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

        # 格式化记忆年龄
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
## 将基于时间的加权应用于搜索结果 {#apply-time-based-weighting-to-search-results}

### 纯语义搜索的问题 {#the-problem-with-pure-semantic-search}

传统的语义搜索仅根据与查询的相似度对结果进行排名。然而，这忽略了一个重要因素：**时间相关性**。一个与您的查询完全匹配但创建于6个月前的记忆，可能不如一个稍微不那么相似但昨天创建的记忆有用。

### 解决方案：组合评分 {#the-solution-combined-scoring}

通过将语义相似度与保留分数相结合，我们创建了一个更智能的排名系统：

**组合评分 = 语义相似度 × 保留分数**

这种方法确保：
- 高度相关且最近的记忆排名最高
- 即使语义相似，时间非常久远的记忆也会被降级
- 最近的记忆即使相似度一般也会得到提升

### 何时使用加权搜索 {#when-to-use-weighted-search}

基于时间的加权特别适用于以下场景：

- **用户偏好**：最近的偏好更可能是当前的
- **学习系统**：最近的课程比旧课程更相关
- **对话式AI**：最近的上下文比历史上下文更重要
- **动态信息**：当信息随时间变化时（价格、可用性等）

### 实现策略 {#implementation-strategy-1}

加权搜索功能的实现步骤：

1. **执行标准搜索**：根据相似度获取候选结果
2. **计算保留分数**：确定每个结果的新鲜度
3. **组合评分**：将相似度与保留分数相乘
4. **重新排序结果**：根据组合评分而非仅相似度排序
5. **返回最佳结果**：提供最相关且最新的记忆

### 调整加权策略 {#tuning-the-weighting}

您可以调整加权策略：

- **纯相似度**：仅使用语义相似度（所有记忆的保留分数 = 1.0）
- **平衡模式**：相似度 × 保留分数（当前方法）
- **最近性提升**：为最近的记忆添加额外加分：`相似度 + (保留分数 × 0.2)`
- **阈值过滤**：过滤掉低于保留分数阈值的记忆

让我们通过应用基于保留分数的加权来优化搜索结果：
```python
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

        # 从 metadata 中获取创建时间
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
memory = Memory(config=auto_config())
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
## 可视化遗忘曲线 {#visualize-the-forgetting-curve}

### 为什么要可视化？ {#why-visualize}

可视化遗忘曲线可以帮助你：

- **理解模式**：观察记忆保留如何随时间衰减
- **识别关键间隔**：发现遗忘最严重的时间段
- **规划复习时间表**：确定最佳复习时机
- **传达概念**：与团队成员或利益相关者分享见解

### 关键可视化元素 {#key-visualization-elements}

一个好的遗忘曲线可视化应包括：

1. **平滑曲线**：展示连续的衰减模式
2. **关键里程碑**：标记重要的时间点（20分钟、1小时、1天等）
3. **保留百分比**：显示每个里程碑的具体保留值
4. **清晰标签**：使时间轴和保留轴易于阅读
5. **专业样式**：使用增强可读性的颜色和格式

### 解读可视化 {#interpreting-the-visualization}

在查看曲线时：

- **陡峭的初始下降**：注意记忆在最初几小时内迅速下降
- **稳定化**：观察曲线在第一天后如何趋于平缓
- **最低阈值**：注意曲线在何处趋于平稳（在我们的模型中为20%）
- **复习窗口**：识别适合间隔重复复习的最佳时间点

### 自定义选项 {#customization-options-1}

你可以自定义可视化内容：

- **时间范围**：扩展到31天以上以进行长期分析
- **衰减参数**：调整以显示不同的遗忘速率
- **多条曲线**：并排比较不同的衰减模型
- **交互式图表**：使用像 Plotly 这样的库进行交互式探索

让我们创建一个遗忘曲线的可视化：
```python
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
## 实现间隔重复系统 {#implement-spaced-repetition-system}

### 什么是间隔重复？ {#what-is-spaced-repetition}

间隔重复是一种学习技术，涉及以递增的时间间隔复习信息。其原理很简单：在你即将遗忘之前复习记忆，这样可以增强记忆的保持力并延长遗忘曲线。

### 如何与遗忘曲线协同工作 {#how-it-works-with-the-forgetting-curve}

遗忘曲线告诉我们，当记忆保持率下降到有用阈值以下时，间隔重复利用这些信息来：

1. **检测低保持率**：识别已经显著衰退的记忆
2. **安排复习**：建议每个记忆的最佳复习时间
3. **重置曲线**：复习后，记忆的保持率重置，延长其有效寿命
4. **优化间隔**：使用逐渐延长的复习间隔

### 复习时间表策略 {#review-schedule-strategy}

基于艾宾浩斯的研究，最佳复习间隔为：

- **20分钟**：第一次复习（捕捉早期遗忘）
- **1小时**：第二次复习（强化初步学习）
- **9小时**：第三次复习（防止显著的过夜遗忘）
- **1天**：第四次复习（每日强化）
- **2-6天**：后续复习（延长记忆保持）
- **2-4周**：长期复习（维持知识）

### 实现组件 {#implementation-components}

一个间隔重复系统需要：

1. **保持率计算**：确定每个记忆的当前保持率
2. **复习时间表**：定义最佳复习间隔
3. **复习检测**：识别当前需要复习的记忆
4. **下次复习预测**：计算每个记忆的下次复习时间
5. **复习跟踪**：在复习后更新 `last_reviewed` 时间戳

### 最佳实践 {#best-practices-1}

在实现间隔重复时：

- **设置阈值**：定义复习所需的最低保持率（例如，50%）
- **跟踪复习**：在每次复习后用 `last_reviewed` 时间戳更新元数据
- **自适应间隔**：根据复习成功/失败调整间隔
- **批量复习**：按复习时间分组记忆以提高处理效率
- **用户偏好**：允许用户将记忆标记为“掌握”，以跳过复习

### 使用场景 {#use-cases-1}

间隔重复非常适合：

- **学习系统**：语言学习、技能获取、知识保持
- **培训项目**：员工入职培训、认证准备
- **个人知识库**：保持对存储信息的熟悉度
- **AI辅导系统**：优化与学生复习概念的时间

基于遗忘曲线，我们可以实现一个间隔重复系统，建议何时复习记忆：
```python
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

    # 优先使用 last_reviewed，否则使用 created_at
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
memory = Memory(config=auto_config())
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
## 最佳实践和提示 {#best-practices-and-tips}

### 何时使用遗忘曲线功能 {#when-to-use-forgetting-curve-features}

遗忘曲线方法在以下情况下最有价值：

- **信息随时间变化**：用户偏好、产品可用性、价格
- **近期性重要**：近期上下文比历史上下文更相关
- **学习系统**：构建教育或培训应用程序
- **大型记忆存储**：当您有大量记忆并需要智能优先级排序时

### 性能考量 {#performance-considerations}

- **元数据存储**：存储时间戳增加的开销很小
- **计算成本**：保留率计算速度快（简单的数学运算）
- **搜索优化**：加权搜索可能需要初始获取更多候选项
- **索引**：考虑为元数据字段建立索引以加快时间戳查询速度

### 常见陷阱 {#common-pitfalls}

1. **缺少时间戳**：在添加记忆时始终存储 `created_at`
2. **时区问题**：对所有时间戳使用 UTC 或一致的时区
3. **过度强调近期性**：不要完全忽略可能仍然相关的旧记忆
4. **固定阈值**：根据您的使用场景调整保留阈值

### 高级技巧 {#advanced-techniques}

- **自适应衰减**：根据记忆的重要性或类型调整衰减速率
- **基于访问的保留**：在记忆被访问时（而不仅仅是被复习时）重置保留
- **类别特定曲线**：为不同的记忆类别使用不同的衰减速率
- **机器学习**：根据用户行为训练模型预测最佳复习时间
