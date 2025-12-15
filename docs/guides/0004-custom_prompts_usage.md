# Custom Prompts Usage Guide

This guide provides detailed instructions on how to use custom prompts in powermem to customize the behavior of various memory operations.

## Overview

powermem supports customizing prompts at multiple levels, including:

1. **Fact Extraction** - Extract factual information from conversations
2. **Memory Update** - Determine how to update existing memories
3. **Importance Evaluation** - Evaluate the importance of memories
4. **Graph Memory Operations** - Operations on graph-structured memories (extract relations, update, delete)

## Configuration Methods

### Method 1: Using MemoryConfig Object (Recommended)

```python
from powermem import Memory, MemoryConfig, GraphStoreConfig, LlmConfig, EmbedderConfig, VectorStoreConfig, IntelligentMemoryConfig

# Create configuration object
config = MemoryConfig(
    # Custom prompt for fact extraction
    custom_fact_extraction_prompt="""
You are an information extraction expert. Extract user preferences, important facts, and plans from conversations.

Rules:
1. Only extract explicitly mentioned information
2. Preserve temporal information (e.g., "yesterday", "last month")
3. Extract related facts separately

Output JSON format: {"facts": ["fact1", "fact2"]}
""",
    
    # Custom prompt for memory update
    custom_update_memory_prompt="""
You are a memory manager. Compare new facts with existing memories and decide: ADD (new), UPDATE (update), DELETE (delete), or NONE (no change).

Important rules:
- If new fact contains temporal info that old memory doesn't, then UPDATE
- If information completely conflicts, then DELETE
- If information is the same, then NONE
""",
    
    # Custom prompt for importance evaluation
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
    
    # Graph memory configuration (optional)
    graph_store=GraphStoreConfig(
        enabled=True,
        
        # Custom prompt for relation extraction
        custom_extract_relations_prompt="""
Extract entities and relationships from text to build a knowledge graph.

Special rules:
- Use "USER_ID" as the entity for user self-references (e.g., "I", "me", "my")
- Only extract explicitly mentioned relationships
- Maintain consistency and generality in relationship types

Extracted relationships should follow this format:
source -- relationship -- destination
""",
        
        # Custom prompt for graph update
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
        
        # Custom prompt for deleting relations
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
        
        # Or use generic prompt (for relation extraction)
        # custom_prompt="Your custom prompt..."
    ),
    
    # Other configurations...
    llm=LlmConfig(provider="openai", config={"api_key": "..."}),
    embedder=EmbedderConfig(provider="openai", config={"api_key": "..."}),
    vector_store=VectorStoreConfig(provider="chroma", config={})
)

# Create Memory instance
memory = Memory(config=config)
```

### Method 2: Using Dictionary Configuration

```python
from powermem import Memory

# Dictionary configuration (no need to import config classes)

config = {
    # Custom prompt for fact extraction
    "custom_fact_extraction_prompt": """
You are an information extraction expert. Extract user preferences, important facts, and plans from conversations.
Output JSON format: {"facts": ["fact1", "fact2"]}
""",
    
    # Custom prompt for memory update
    "custom_update_memory_prompt": """
You are a memory manager. Compare new facts with existing memories and decide: ADD, UPDATE, DELETE, or NONE.
""",
    
    # Custom prompt for importance evaluation
    "custom_importance_evaluation_prompt": """
Evaluate the importance of memory content on a scale from 0.0 to 1.0.
Return JSON format containing importance_score and reasoning.
""",
    
    # Graph memory configuration
    "graph_store": {
        "enabled": True,
        "custom_extract_relations_prompt": "Extract entities and relationships from text...",
        "custom_update_graph_prompt": "Update graph memories...",
        "custom_delete_relations_prompt": "Delete relationships...",
        # Or
        "custom_prompt": "Generic prompt (for relation extraction)"
    },
    
    # Other configurations...
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

## Detailed Examples

### Example 1: Custom Fact Extraction

```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# Create a prompt focused on extracting preferences and interests
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

# Add memory, system will use custom fact extraction prompt
result = memory.add(
    messages=[
        {"role": "user", "content": "I like latte coffee, I need a cup every morning."},
        {"role": "assistant", "content": "Got it, I've remembered your coffee preference."}
    ],
    user_id="user123",
    infer=True  # Enable intelligent extraction
)

print(result)
```

### Example 2: Custom Memory Update Strategy

```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# Create a more conservative update strategy
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

# System will use custom update strategy
memory.add(messages="I like Python programming", user_id="user123")
memory.add(messages="I love Python and JavaScript programming", user_id="user123")  # Will update instead of add new
```

### Example 3: Custom Importance Evaluation

```python
from powermem import Memory, MemoryConfig, IntelligentMemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# Create an evaluation prompt that emphasizes emotional value and practicality
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

# System will use custom importance evaluation
memory.add(
    messages="I have diabetes and need to monitor blood sugar control",
    user_id="user123",
    metadata={"category": "health", "priority": "critical"}
)
```

### Example 4: Custom Graph Memory Operations

```python
from powermem import Memory, MemoryConfig, GraphStoreConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

config = MemoryConfig(
    graph_store=GraphStoreConfig(
        enabled=True,
        provider="oceanbase",
        
        # Relation extraction: Focus on extracting person relationships
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
        
        # Graph update: Intelligent relationship merging
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
        
        # Delete relationships: Conservative strategy
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
            "host": "127.0.0.1",
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

# Add memory, system will use custom graph memory prompts
memory.add(
    messages="Alice is my friend, we work together at Google. She likes to drink coffee.",
    user_id="user123",
    agent_id="agent1"
)
```

## Prompt Placeholders

Different prompts support different placeholders:

### Fact Extraction Prompt
- No specific placeholders needed, system will automatically add conversation content

### Memory Update Prompt
- `{old_memory}` or get existing memories from config
- `{new_facts}` or get new facts from config

### Importance Evaluation Prompt
- `{content}` - Content to evaluate
- `{metadata}` - Metadata (optional)
- `{context}` - Context (optional)

### Graph Update Prompt
- `{existing_memories}` - Existing graph memories
- `{new_memories}` - New graph memories

### Relation Extraction Prompt
- `USER_ID` - Will be replaced with actual user ID
- `CUSTOM_PROMPT` - If `custom_prompt` is provided, this placeholder will be replaced

### Delete Relations Prompt
- `USER_ID` - Will be replaced with actual user ID

## Prompt Writing Guidelines

### 1. Clarity
- Use clear, specific instructions
- Avoid ambiguous expressions

### 2. Format Specification
- Clearly specify output format (JSON, etc.)
- Provide example formats

### 3. Edge Cases
- Explain how to handle conflicting information
- Define conditions for deletion/update

### 4. Context Preservation
- Require preservation of temporal information
- Require preservation of relevant details

### 5. Consistency
- Use consistent terminology
- Maintain uniform format

## Complete Example: Multi-language Support

```python
from powermem import Memory, MemoryConfig, LlmConfig, EmbedderConfig, VectorStoreConfig

# Chinese prompt configuration
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

# Use Chinese conversations
memory_cn.add(
    messages=[
        {"role": "user", "content": "我昨天去了北京，参观了故宫。"},
        {"role": "assistant", "content": "听起来很有意思！"}
    ],
    user_id="user_cn",
    infer=True
)
```

## Best Practices

1. **Test Prompts**: Test prompt effectiveness with small datasets before production use
2. **Iterative Optimization**: Continuously optimize prompts based on results
3. **Version Control**: Save prompts in configuration files for version management
4. **Documentation**: Document the purpose and special rules of each custom prompt
5. **Performance Considerations**: Concise prompts can reduce token consumption and improve speed

## Important Notes

1. **Backward Compatibility**: If custom prompts are not provided, system will use default prompts
2. **Format Requirements**: Ensure custom prompts contain necessary placeholders
3. **LLM Compatibility**: Some prompt formats may be more effective for specific LLMs
4. **Configuration Priority**:
   - `custom_extract_relations_prompt` > `custom_prompt` > default prompt
   - Top-level config will be merged into sub-configs

## Troubleshooting

### Issue: Custom prompts not taking effect
- Check if configuration is correctly passed
- Verify field names are spelled correctly
- Check logs to confirm prompts are being read

### Issue: Prompt format errors
- Ensure necessary placeholders are included
- Check if JSON format is correct
- Verify string escaping

### Issue: LLM responses don't match expectations
- Adjust prompt clarity
- Add more examples
- Check LLM response format settings

## Summary

Through custom prompts, you can:
- Customize extraction rules for specific domains
- Control memory update strategies
- Adjust importance evaluation criteria
- Customize graph memory operation methods

This makes powermem adaptable to various use cases and requirements.
