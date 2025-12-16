"""
User profile extraction prompts

This module provides prompts for extracting user profile information from conversations.
"""

import json
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


# User profile topics for reference in prompt
USER_PROFILE_TOPICS = """
- Basic Information  
  - User Name  
  - User Age (integer)  
  - Gender  
  - Date of Birth  
  - Nationality  
  - Ethnicity  
  - Language  

- Contact Information  
  - Email  
  - Phone  
  - City  
  - Province  

- Education Background  
  - School  
  - Degree  
  - Major  
  - Graduation Year  

- Demographics  
  - Marital Status  
  - Number of Children  
  - Household Income  

- Employment  
  - Company  
  - Position  
  - Work Location  
  - Projects Involved In  
  - Work Skills  

- Interests and Hobbies  
  - Books  
  - Movies  
  - Music  
  - Food  
  - Sports  

- Lifestyle  
  - Dietary Preferences (e.g., vegetarian, vegan)  
  - Exercise Habits  
  - Health Status  
  - Sleep Patterns  
  - Smoking  
  - Alcohol Consumption  

- Psychological Traits  
  - Personality Traits  
  - Values  
  - Beliefs  
  - Motivations  
  - Goals  

- Life Events  
  - Marriage  
  - Relocation  
  - Retirement
"""


USER_PROFILE_EXTRACTION_PROMPT = f"""You are a user profile extraction specialist. Your task is to analyze conversations and extract user profile information.

[Reference Topics]:
The following topics are for guidance only. Please selectively extract information based on the actual content of the conversation, without forcing all fields to be filled.:
{USER_PROFILE_TOPICS}

[Instructions]:
1. Review the current user profile if provided below
2. Analyze the new conversation carefully to identify any new or updated user-related information
3. Extract only factual information explicitly mentioned in the conversation
4. Update the profile by:
   - Adding new information that is not in the current profile
   - Updating existing information if the conversation provides more recent or different details
   - Keeping unchanged information that is still valid
5. Combine all information into a coherent, updated profile description
6. If no relevant profile information is found in the conversation, return the current profile as-is
7. Write the profile in natural language, not as structured data
8. Focus on current state and characteristics of the user
9. If no user profile information can be extracted from the conversation at all, return an empty string ""
10. The final extracted profile description must not exceed 1,000 characters. If it does, compress the content concisely without losing essential factual information.
"""


def get_user_profile_extraction_prompt(conversation: str, existing_profile: Optional[str] = None) -> Tuple[str, str]:
    """
    Generate the system prompt and user message for user profile extraction.
    
    Args:
        conversation: The conversation text to analyze
        existing_profile: Optional existing user profile content to update
        
    Returns:
        Tuple of (system_prompt, user_message):
        - system_prompt: Fixed instructions and context for the LLM
        - user_message: The conversation text to analyze
    """
    # Build the prompt with optional Current User Profile section
    current_profile_section = ""
    if existing_profile:
        current_profile_section = f"""

[Current User Profile]:
```
{existing_profile}
```"""
    
    system_prompt = f"""{USER_PROFILE_EXTRACTION_PROMPT}{current_profile_section}

[Target]:
Extract and return the user profile information as a text description:"""
    user_message = conversation
    
    return system_prompt, user_message



def get_user_profile_topics_extraction_prompt(
    conversation: str,
    existing_topics: Optional[Dict[str, Any]] = None,
    custom_topics: Optional[str] = None,
    strict_mode: bool = False,
) -> Tuple[str, str]:
    """
    Generate the system prompt and user message for structured topic extraction.

    Args:
        conversation: The conversation text to analyze
        existing_topics: Optional existing structured topics dictionary to update
        custom_topics: Optional custom topics JSON string. Format should be:
            {
                "main_topic": {
                    "sub_topic1": "description1",
                    "sub_topic2": "description2"
                }
            }
            - All keys must be in snake_case (lowercase, underscores, no spaces)
            - Descriptions are for reference only and should NOT be used as keys in the output
        strict_mode: If True, only output topics from the provided list; if False, can extend

    Returns:
        Tuple of (system_prompt, user_message):
        - system_prompt: Fixed instructions and context for the LLM
        - user_message: The conversation text to analyze
    """
    # Use custom topics if provided, otherwise use default
    if custom_topics:
        # Parse JSON string
        try:
            if isinstance(custom_topics, str):
                topics_dict = json.loads(custom_topics)
            else:
                topics_dict = custom_topics
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid custom_topics JSON format: {e}")

        if not isinstance(topics_dict, dict):
            raise ValueError("custom_topics must be a JSON object (dictionary)")

        # Format topics as JSON for display (no conversion, use as-is)
        formatted_topics = json.dumps(topics_dict, ensure_ascii=False, indent=2)
        has_descriptions = True
    else:
        # Use default USER_PROFILE_TOPICS as-is
        formatted_topics = USER_PROFILE_TOPICS
        has_descriptions = False

    # Build strict mode instruction
    if strict_mode:
        strict_instruction = """
CRITICAL: You MUST only output topics that are listed in the [Available Topics] section above. 
Do NOT create new topics or use different topic names. If information doesn't fit any listed topic, 
you may omit it or place it under the most relevant existing topic."""
    else:
        strict_instruction = """
You may extend the topic structure if needed, but try to use the provided topics when possible. 
If you add new topics, use snake_case format (lowercase with underscores)."""

    # Build description warning if custom_topics has descriptions
    description_warning = ""
    if custom_topics and has_descriptions:
        description_warning = """
IMPORTANT: The descriptions shown in [Available Topics] are for reference only to help you understand what each topic represents.
DO NOT use the descriptions as keys in your output. Only use the topic names (main_topic and sub_topic) as keys.
For example, if you see "user_name: The user's full name", use "user_name" as the key, NOT "The user's full name"."""

    # Build existing topics section
    existing_topics_section = ""
    if existing_topics:
        existing_topics_json = json.dumps(existing_topics, ensure_ascii=False, indent=2)
        existing_topics_section = f"""

[Current User Topics]:
```json
{existing_topics_json}
```"""

    # Build the prompt
    if custom_topics:
        topics_section = f"""[Available Topics]:
The following JSON structure defines the available topics for extraction:
```json
{formatted_topics}
```
"""
    else:
        topics_section = f"""[Available Topics]:
The following topics are for reference. All topic keys in your output must be in snake_case format (lowercase with underscores):
{formatted_topics}
"""

    system_prompt = f"""You are a user profile topic extraction specialist. Your task is to analyze conversations and extract user profile information as structured topics.

{topics_section}{description_warning}

[Instructions]:
1. Review the current user topics if provided below
2. Analyze the new conversation carefully to identify any new or updated user-related information
3. Extract only factual information explicitly mentioned in the conversation
4. Update the topics by:
   - Adding new information that is not in the current topics
   - Updating existing information if the conversation provides more recent or different details
   - Keeping unchanged information that is still valid
5. Structure the output as a JSON object with hierarchical topics (main topics as keys, sub-topics as nested objects)
6. All keys must be in snake_case format (lowercase with underscores)
7. If no relevant profile information is found in the conversation, return the current topics as-is
8. If no user profile information can be extracted from the conversation at all, return an empty JSON object {{}}
9. Focus on current state and characteristics of the user
{strict_instruction}{existing_topics_section}

[Output Format]:
Return a valid JSON object with the following structure:
{{
  "main_topic_name": {{
    "sub_topic_name": "value",
    "another_sub_topic": "value"
  }},
  "another_main_topic": {{
    "sub_topic": "value"
  }}
}}

All keys must be in snake_case (lowercase with underscores). Values can be strings, numbers, or nested objects as needed.
Remember: Use only the topic names as keys, NOT the descriptions."""

    user_message = conversation

    return system_prompt, user_message

