"""
Prompt templates for memory operations

This module provides prompt templates for different memory operations.
"""

from .templates import PromptTemplates
from .graph.graph_prompts import GraphPrompts
from .graph.graph_tools_prompts import GraphToolsPrompts
from .intelligent_memory_prompts import (
    FACT_RETRIEVAL_PROMPT,
    FACT_EXTRACTION_PROMPT,
    DEFAULT_UPDATE_MEMORY_PROMPT,
    MEMORY_UPDATE_PROMPT,
    get_memory_update_prompt,
    parse_messages_for_facts
)
from .user_profile_prompts import (
    USER_PROFILE_TOPICS,
    USER_PROFILE_EXTRACTION_PROMPT,
    get_user_profile_extraction_prompt,
)

__all__ = [
    "PromptTemplates",
    "GraphPrompts",
    "GraphToolsPrompts",
    "FACT_RETRIEVAL_PROMPT",
    "FACT_EXTRACTION_PROMPT",
    "DEFAULT_UPDATE_MEMORY_PROMPT",
    "MEMORY_UPDATE_PROMPT",
    "get_memory_update_prompt",
    "parse_messages_for_facts",
    "USER_PROFILE_TOPICS",
    "USER_PROFILE_EXTRACTION_PROMPT",
    "get_user_profile_extraction_prompt",
]
