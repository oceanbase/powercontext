"""
User profile extraction prompts

This module provides prompts for extracting user profile information from conversations.
"""

import logging
from typing import Optional, Tuple

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

