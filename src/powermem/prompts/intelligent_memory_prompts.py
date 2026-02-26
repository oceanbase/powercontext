"""
Intelligent memory prompts for fact extraction and memory consolidation

This module provides prompts for intelligent memory operations including:
1. Fact extraction from conversations
2. Memory consolidation and deduplication
3. Memory update operations (ADD/UPDATE/DELETE)

"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


# Use  FACT_RETRIEVAL_PROMPT for compatibility
FACT_RETRIEVAL_PROMPT = f"""You are a Personal Information Organizer. Extract relevant facts, memories, preferences, intentions, and needs from conversations into distinct, manageable facts.

Information Types: Personal preferences, details (names, relationships, dates), plans, intentions, needs, requests, activities, health/wellness (including medical appointments, symptoms, treatments), professional, miscellaneous.

CRITICAL Rules:
1. TEMPORAL: ALWAYS extract time info (dates, relative refs like "yesterday", "last week"). Include in facts (e.g., "Went to Hawaii in May 2023" or "Went to Hawaii last year", not just "Went to Hawaii"). Preserve relative time refs for later calculation.
2. COMPLETE: Extract self-contained facts with who/what/when/where when available.
3. SEPARATE: Extract distinct facts separately, especially when they have different time periods.
4. INTENTIONS & NEEDS: ALWAYS extract user intentions, needs, and requests even without time information. Examples: "Want to book a doctor appointment", "Need to call someone", "Plan to visit a place".
5. LANGUAGE: DO NOT translate. Preserve the original language of the source text for each extracted fact. If the input is Chinese, output facts in Chinese; if English, output in English; if mixed-language, keep each fact in the language it appears in.

Examples:
Input: Hi.
Output: {{"facts" : []}}

Input: Yesterday, I met John at 3pm. We discussed the project.
Output: {{"facts" : ["Met John at 3pm yesterday", "Discussed project with John yesterday"]}}

Input: Last May, I went to India. Visited Mumbai and Goa.
Output: {{"facts" : ["Went to India in May", "Visited Mumbai in May", "Visited Goa in May"]}}

Input: I met Sarah last year and became friends. We went to movies last month.
Output: {{"facts" : ["Met Sarah last year and became friends", "Went to movies with Sarah last month"]}}

Input: I'm John, a software engineer.
Output: {{"facts" : ["Name is John", "Is a software engineer"]}}

Input: I want to book an appointment with a cardiologist.
Output: {{"facts" : ["Want to book an appointment with a cardiologist"]}}

Rules:
- Today: {datetime.now().strftime("%Y-%m-%d")}
- Return JSON: {{"facts": ["fact1", "fact2"]}}
- Extract from user/assistant messages only
- Extract intentions, needs, and requests even without time information
- If no relevant facts, return empty list
- Output must preserve the input language (no translation)

Extract facts from the conversation below:"""

# Alias for compatibility
FACT_EXTRACTION_PROMPT = FACT_RETRIEVAL_PROMPT


DEFAULT_UPDATE_MEMORY_PROMPT = """You are a memory manager. Compare new facts with existing memory. Decide: ADD, UPDATE, DELETE, or NONE.

Operations:
1. **ADD**: New info not in memory -> add with new ID
2. **UPDATE**: Info exists but different/enhanced -> update (keep same ID). Prefer fact with most information.
3. **DELETE**: Contradictory info -> delete (use sparingly)
4. **NONE**: Already present or irrelevant -> no change

Temporal Rules (CRITICAL):
- New fact has time info, memory doesn't -> UPDATE memory to include time
- Both have time, new is more specific/recent -> UPDATE to new time
- Time conflicts (e.g., "2022" vs "2023") -> UPDATE to more recent
- Preserve relative time refs (e.g., "last year", "two months ago")
- When merging, combine temporal info: "Met Sarah" + "Met Sarah last year" -> UPDATE to "Met Sarah last year"

Examples:
Add: Memory: [{{"id":"0","text":"User is engineer"}}], Facts: ["Name is John"]
-> [{{"id":"0","text":"User is engineer","event":"NONE"}}, {{"id":"1","text":"Name is John","event":"ADD"}}]

Update (time): Memory: [{{"id":"0","text":"Went to Hawaii"}}], Facts: ["Went to Hawaii in May 2023"]
-> [{{"id":"0","text":"Went to Hawaii in May 2023","event":"UPDATE","old_memory":"Went to Hawaii"}}]

Update (enhance): Memory: [{{"id":"0","text":"Likes cricket"}}], Facts: ["Loves cricket with friends"]
-> [{{"id":"0","text":"Loves cricket with friends","event":"UPDATE","old_memory":"Likes cricket"}}]

Delete: Only clear contradictions (e.g., "Loves pizza" vs "Dislikes pizza"). Prefer UPDATE for time conflicts.

Important: Use existing IDs only. Keep same ID when updating. Always preserve temporal information.
LANGUAGE (CRITICAL): Do NOT translate memory text. Keep the same language as the incoming fact(s) and the original memory whenever possible.
"""

# Alias for compatibility
MEMORY_UPDATE_PROMPT = DEFAULT_UPDATE_MEMORY_PROMPT


def get_memory_update_prompt(
    retrieved_old_memory: list, 
    new_facts: list, 
    custom_prompt: Optional[str] = None
) -> str:
    """
    Generate the prompt for memory update operations.
    
    Args:
        retrieved_old_memory: List of existing memories with id and text
        new_facts: List of newly extracted facts
        custom_prompt: Optional custom prompt template
        
    Returns:
        Complete prompt string for LLM
    """
    if custom_prompt is None:
        custom_prompt = DEFAULT_UPDATE_MEMORY_PROMPT
    
    if retrieved_old_memory:
        current_memory_part = f"Current memory:\n```\n{retrieved_old_memory}\n```\n"
    else:
        current_memory_part = "Current memory is empty.\n"
    
    # Format new facts
    new_facts_str = "\n".join([f"- {fact}" for fact in new_facts])
    
    return f"""{custom_prompt}

{current_memory_part}New facts:
```
{new_facts_str}
```

Return JSON only:
{{
    "memory": [
        {{
            "id": "<existing ID for update/delete, new ID for add>",
            "text": "<memory content>",
            "event": "ADD|UPDATE|DELETE|NONE",
            "old_memory": "<old content, required for UPDATE>"
        }}
    ]
}}
"""


def parse_messages_for_facts(messages: list) -> str:
    """
    Parse messages into a format suitable for fact extraction.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content'
        
    Returns:
        Formatted string representation of the conversation
    """
    if isinstance(messages, str):
        return messages
    
    if not isinstance(messages, list):
        return str(messages)
    
    conversation = ""
    for msg in messages:
        if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
            role = msg['role']
            content = msg['content']
            if role != "system":  # Skip system messages
                conversation += f"{role}: {content}\n"
    
    return conversation
