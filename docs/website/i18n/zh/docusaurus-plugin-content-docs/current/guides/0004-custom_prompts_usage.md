---
sidebar_label: 自定义 prompt 使用指南
---

# 自定义 prompt 使用指南 {#custom-prompts-usage-guide}

本指南提供了在 powermem 中使用自定义 prompt 来定制各种记忆操作行为的详细说明。

## 概述 {#overview}

powermem 支持在多个层面上自定义 prompt，包括：

1. **Fact Extraction（事实提取）** - 从对话中提取事实信息
2. **Memory Update（记忆更新）** - 决定如何更新现有记忆
3. **Importance Evaluation（重要性评估）** - 评估记忆的重要性
4. **Graph Memory Operations（图记忆操作）** - 针对图结构记忆的操作（提取关系、更新、删除）

## 配置方法 {#configuration-methods}

### 方法 1：使用 MemoryConfig 对象（推荐） {#method-1-using-memoryconfig-object-recommended}
```python
from powermem import Memory, MemoryConfig, GraphStoreConfig, LlmConfig, EmbedderConfig, VectorStoreConfig, IntelligentMemoryConfig

# 创建配置对象
config = MemoryConfig(
    # 自定义事实提取 Prompt
    custom_fact_extraction_prompt="""
You are an information extraction expert. Extract user preferences, important facts, and plans from conversations.

Rules:
1. Only extract explicitly mentioned information
2. Preserve temporal information (e.g., "yesterday", "last month")
3. Extract related facts separately

Output JSON format: {"facts": ["fact1", "fact2"]}
""",

    # 自定义记忆更新 Prompt
    custom_update_memory_prompt="""
You are a memory manager. Compare new facts with existing memories and decide: ADD (new), UPDATE (update), DELETE (delete), or NONE (no change).

Important rules:
- If new fact contains temporal info that old memory doesn't, then UPDATE
- If information completely conflicts, then DELETE
- If information is the same, then NONE
""",

    # 自定义重要性评估 Prompt
    custom_importance_evaluation_prompt="""
Evaluate the importance of memory content on a scale from 0.0 to 1.0.

Evaluation criteria:
- Relevance: How relevant to user needs
- Novelty: Information uniqueness
- Emotional Impact: Emotional significance
- Actionability: Information usefulness

Return JSON format:
{
    "importance_score": <0.0-1.0>,
    "reasoning": "<explanation>",
    "criteria_scores": {
        "relevance": <score>,
        "novelty": <score>,
        "emotional_impact": <score>,
        "actionable": <score>
    }
}
""",

    # 图记忆配置（可选）
    graph_store=GraphStoreConfig(
        enabled=True,

        # 自定义关系提取 Prompt
        custom_extract_relations_prompt="""
Extract entities and relationships from text to build a knowledge graph.

Special rules:
- Use "USER_ID" as the entity for user self-references (e.g., "I", "me", "my")
- Only extract explicitly mentioned relationships
- Maintain consistency and generality in relationship types

Extracted relationships should follow this format:
source -- relationship -- destination
""",

        # 自定义图更新 Prompt
        custom_update_graph_prompt="""
Analyze existing graph memories and new information, update relationships.

Existing memories:
{existing_memories}

New memories:
{new_memories}

Rules:
1. If new information conflicts with existing memory, update the relationship
2. If new information is more detailed, update the relationship
3. Maintain graph structure consistency
""",

        # 自定义删除关系 Prompt
        custom_delete_relations_prompt="""
Analyze existing relationships and determine which should be deleted.

Existing relationships:
- Only delete outdated or conflicting relationships
- If new information contradicts existing relationship, then delete
- Do not delete if there is a possibility of same type relationship with different destination nodes

Example:
Existing: Alice -- loves_to_eat -- pizza
New Information: Alice also loves to eat burger
Result: Do not delete (Alice can love both pizza and burger)
""",

        # 或使用通用 Prompt（用于关系提取）
        # custom_prompt="您的自定义 Prompt..."
    ),

    # 其他配置...
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

# 创建 Memory 实例
memory = Memory(config=config)
```
### 方法 2：使用环境变量 {#method-2-using-environment-variables}

在您的 `.env` 文件或 shell 中设置环境变量。这些变量会被 `auto_config()` / `create_memory()` 自动识别，无需任何代码更改。
```bash
# .env
POWERMEM_CUSTOM_FACT_EXTRACTION_PROMPT=You are an information extraction expert. Extract user preferences, important facts, and plans from conversations. Output JSON format: {"facts": ["fact1", "fact2"]}

POWERMEM_CUSTOM_UPDATE_MEMORY_PROMPT=You are a memory manager. Compare new facts with existing memories and decide: ADD, UPDATE, DELETE, or NONE.

POWERMEM_CUSTOM_IMPORTANCE_EVALUATION_PROMPT=Evaluate the importance of memory content on a scale from 0.0 to 1.0. Return JSON containing importance_score and reasoning.
```

```python
from powermem import create_memory

# 从环境变量自动加载 Prompt
memory = create_memory()
```
> **注意：** 在 `.env` 文件中使用多行 prompt 时，请使用单行格式或以编程方式加载值。环境变量配置最适合用于短的单行 prompt 覆盖，或在配置文件不方便的部署环境中使用。

### 方法 3：使用字典配置 {#method-3-using-dictionary-configuration}
```python
from powermem import Memory

# 字典配置（无需导入配置类）

config = {
    # 自定义事实提取 Prompt
    "custom_fact_extraction_prompt": """
You are an information extraction expert. Extract user preferences, important facts, and plans from conversations.
Output JSON format: {"facts": ["fact1", "fact2"]}
""",

    # 自定义记忆更新 Prompt
    "custom_update_memory_prompt": """
You are a memory manager. Compare new facts with existing memories and decide: ADD, UPDATE, DELETE, or NONE.
""",

    # 自定义重要性评估 Prompt
    "custom_importance_evaluation_prompt": """
Evaluate the importance of memory content on a scale from 0.0 to 1.0.
Return JSON format containing importance_score and reasoning.
""",

    # 图记忆配置
    "graph_store": {
        "enabled": True,
        "custom_extract_relations_prompt": "Extract entities and relationships from text...",
        "custom_update_graph_prompt": "Update graph memories...",
        "custom_delete_relations_prompt": "Delete relationships...",
        # 或者
        "custom_prompt": "Generic prompt (for relation extraction)"
    },

    # 其他配置...
    "llm": {
        "provider": "openai",
        "config": {"api_key": "..."}
    },
    "embedder": {
        "provider": "openai",
        "config": {"api_key": "..."}
    },
    "vector_store": {
        "provider": "chroma",
        "config": {}
    }
}

memory = Memory(config=config)
```
## 详细示例 {#detailed-examples}

### 示例 1: 自定义事实提取 {#example-1-custom-fact-extraction}
```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# 创建用于提取偏好和兴趣的 Prompt
config = MemoryConfig(
    custom_fact_extraction_prompt="""
You are a preferences and interests extraction expert.

Extract from conversations:
1. User preferences (things they like/dislike)
2. Hobbies and interests
3. Lifestyle choices

Format requirements:
- Each fact should be independent and complete sentence
- Preserve specific details (brands, locations, times)
- Output JSON: {"facts": ["fact list"]}

Conversation content:
""",
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

memory = Memory(config=config)

# 添加记忆，系统将使用自定义事实提取 Prompt
result = memory.add(
    messages=[
        {"role": "user", "content": "I like latte coffee, I need a cup every morning."},
        {"role": "assistant", "content": "Got it, I've remembered your coffee preference."}
    ],
    user_id="user123",
    infer=True  # 启用智能提取
)

print(result)
```
### 示例 2：自定义记忆更新策略 {#example-2-custom-memory-update-strategy}
```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# 创建更保守的更新策略
config = MemoryConfig(
    custom_update_memory_prompt="""
You are a memory manager with a conservative update strategy.

Operation rules:
1. ADD: New information → Add
2. UPDATE: Only update if new information is more specific and detailed
3. DELETE: Only delete if clearly conflicting
4. NONE: Information is same or similar → Keep unchanged

Temporal information handling:
- If new fact has date but old memory doesn't → UPDATE (add time)
- If dates conflict → Keep the latest

Memory format:
{"id": "<id>", "text": "<content>", "event": "ADD|UPDATE|DELETE|NONE"}

Existing memories:
{old_memory}

New facts:
{new_facts}
""",
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

memory = Memory(config=config)

# 系统将使用自定义更新策略
memory.add(messages="I like Python programming", user_id="user123")
memory.add(messages="I love Python and JavaScript programming", user_id="user123")  # 将更新现有记忆，而不是新增一条
```
### 示例 3：自定义重要性评估 {#example-3-custom-importance-evaluation}
```python
from powermem import Memory, MemoryConfig, IntelligentMemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# 创建强调情感价值和实用性的评估 Prompt
config = MemoryConfig(
    custom_importance_evaluation_prompt="""
Evaluate the importance of memory content, focusing on emotional value and practicality.

Scoring weights:
- Emotional Impact: 40% (highest weight)
- Actionability: 30%
- Relevance: 20%
- Novelty: 10%

Scoring criteria:
- 0.8-1.0: Extremely high importance (e.g., passwords, important dates, health information)
- 0.6-0.8: High importance (e.g., preferences, plans, relationships)
- 0.4-0.6: Medium importance (e.g., general information)
- 0.0-0.4: Low importance (e.g., temporary information)

Content: {content}
Metadata: {metadata}

Return JSON:
{{
    "importance_score": <0.0-1.0>,
    "reasoning": "<detailed explanation>",
    "criteria_scores": {{
        "emotional_impact": <0.0-1.0>,
        "actionable": <0.0-1.0>,
        "relevance": <0.0-1.0>,
        "novelty": <0.0-1.0>
    }}
}}
""",
    intelligent_memory=IntelligentMemoryConfig(
        enabled=True
    ),
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

memory = Memory(config=config)

# 系统将使用自定义重要性评估
memory.add(
    messages="I have diabetes and need to monitor blood sugar control",
    user_id="user123",
    metadata={"category": "health", "priority": "critical"}
)
```
### 示例 4：自定义 Graph 记忆操作 {#example-4-custom-graph-memory-operations}
```python
from powermem import Memory, MemoryConfig, GraphStoreConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

config = MemoryConfig(
    graph_store=GraphStoreConfig(
        enabled=True,
        provider="oceanbase",

        # 关系提取：关注人物关系抽取
        custom_extract_relations_prompt="""
Extract entities and relationships from text, with special focus on person relationships.

Extraction rules:
1. Identify all person entities
2. Extract relationships between persons (e.g., friend, colleague, family member)
3. Extract relationships between persons and locations, events
4. Use "USER_ID" to represent the user themselves ("I", "my")

Relationship type examples:
- Interpersonal: friend_of, colleague_of, family_member_of
- Location: lives_in, works_at, visited
- Event: attended, organized, participated_in

Text: {text}
""",

        # 图更新：智能合并关系
        custom_update_graph_prompt="""
Update knowledge graph, intelligently merge and update relationships.

Existing relationships:
{existing_memories}

New relationships:
{new_memories}

Update strategy:
1. If relationship is same but destination is different, keep both (e.g., Alice -- likes -- pizza and Alice -- likes -- burger)
2. If relationship is more specific, update relationship description
3. If time is more recent, update to latest information
4. Avoid duplicates and redundancy
""",

        # 删除关系：保守策略
        custom_delete_relations_prompt="""
Determine relationships to delete using conservative strategy.

Deletion conditions (all must be met):
1. New information explicitly negates existing relationship
2. New information is more recent and accurate
3. Relationship type and destination are both the same

Do not delete if:
- Relationship type is same but destination is different (allow multiple destinations)
- Information is ambiguous or unclear
- Could be supplementary information rather than conflict

Existing relationship list:
- Each relationship format: source -- relationship -- destination

New information:
{new_text}

Only return relationships that need to be deleted.
""",

        config={
            "host": "localhost",
            "port": 2881,
            "user": "root",
            "password": "password",
            "db_name": "test",
            "embedding_model_dims": 768
        }
    ),
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

memory = Memory(config=config)

# 添加记忆，系统将使用自定义图记忆 Prompt
memory.add(
    messages="Alice is my friend, we work together at Google. She likes to drink coffee.",
    user_id="user123",
    agent_id="agent1"
)
```
## prompt 占位符 {#prompt-placeholders}

不同的 prompt 支持不同的占位符：

### Fact Extraction Prompt {#fact-extraction-prompt}
- 不需要特定的占位符，系统会自动添加对话内容

### Memory 更新 Prompt {#memory-update-prompt}
- `{old_memory}` 或从配置中获取现有记忆
- `{new_facts}` 或从配置中获取新事实

### Importance Evaluation Prompt {#importance-evaluation-prompt}
- `{content}` - 要评估的内容
- `{metadata}` - 元数据（可选）
- `{context}` - 上下文（可选）

### Graph Update Prompt {#graph-update-prompt}
- `{existing_memories}` - 现有的图记忆
- `{new_memories}` - 新的图记忆

### Relation Extraction Prompt {#relation-extraction-prompt}
- `USER_ID` - 将被替换为实际的用户 ID
- `CUSTOM_PROMPT` - 如果提供了 `custom_prompt`，此占位符将被替换

### 删除关系 Prompt {#delete-relations-prompt}
- `USER_ID` - 将被替换为实际的用户 ID

## prompt 编写指南 {#prompt-writing-guidelines}

### 1. 清晰性 {#1-clarity}
- 使用清晰、具体的指令
- 避免模糊的表达

### 2. 格式规范 {#2-format-specification}
- 明确指定输出格式（如 JSON 等）
- 提供示例格式

### 3. 边界情况 {#3-edge-cases}
- 说明如何处理冲突信息
- 定义删除/更新的条件

### 4. 上下文保留 {#4-context-preservation}
- 要求保留时间信息
- 要求保留相关细节

### 5. 一致性 {#5-consistency}
- 使用一致的术语
- 保持统一的格式

## 完整示例：多语言支持 {#complete-example-multi-language-support}
```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# 中文 Prompt 配置
chinese_config = MemoryConfig(
    custom_fact_extraction_prompt="""
You are a Chinese information extraction expert. Extract user information from Chinese conversations.

Extraction rules:
1. Extract personal information, preferences, plans
2. Preserve temporal expressions (e.g., "yesterday", "last week")
3. Maintain naturalness of Chinese expressions

Output format: {"facts": ["fact1", "fact2"]}
""",

    custom_update_memory_prompt="""
You are a Chinese memory manager. Compare new facts with existing memories.

Operations: ADD (新增), UPDATE (更新), DELETE (删除), NONE (不变)

Important: Preserve natural Chinese expressions.
""",
    llm=LlmConfig(provider="qwen", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="qwen", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="oceanbase", config={})
)

memory_cn = Memory(config=chinese_config)

# 使用中文对话
memory_cn.add(
    messages=[
        {"role": "user", "content": "我昨天去了北京，参观了故宫。"},
        {"role": "assistant", "content": "听起来很有意思！"}
    ],
    user_id="user_cn",
    infer=True
)
```
## 最佳实践 {#best-practices}

1. **测试 prompt**：在生产使用前，使用小型数据集测试 prompt 的有效性
2. **迭代优化**：根据结果持续优化 prompt
3. **版本控制**：将 prompt 保存在配置文件中以进行版本管理
4. **文档记录**：记录每个自定义 prompt 的用途和特殊规则
5. **性能考量**：简洁的 prompt 可以减少 token 消耗并提高速度

## 重要说明 {#important-notes}

1. **向后兼容性**：如果未提供自定义 prompt，系统将使用默认 prompt
2. **格式要求**：确保自定义 prompt 包含必要的占位符
3. **LLM 兼容性**：某些 prompt 格式可能对特定的 LLM 更有效
4. **配置优先级**：
   - `custom_extract_relations_prompt` > `custom_prompt` > 默认 prompt
   - 顶层配置将合并到子配置中

## 故障排查 {#troubleshooting}

### 问题：自定义 prompt 未生效 {#issue-custom-prompts-not-taking-effect}
- 检查配置是否正确传递
- 验证字段名称拼写是否正确
- 检查日志以确认 prompt 是否被读取

### 问题：prompt 格式错误 {#issue-prompt-format-errors}
- 确保包含必要的占位符
- 检查 JSON 格式是否正确
- 验证字符串转义是否正确

### 问题：LLM 响应不符合预期 {#issue-llm-responses-dont-match-expectations}
- 调整 prompt 的清晰度
- 添加更多示例
- 检查 LLM 响应格式设置

## 总结 {#summary}

通过自定义 prompt，您可以：
- 为特定领域定制提取规则
- 控制记忆更新策略
- 调整重要性评估标准
- 自定义图记忆操作方法

这使得 PowerMem 能够适应各种使用场景和需求。
