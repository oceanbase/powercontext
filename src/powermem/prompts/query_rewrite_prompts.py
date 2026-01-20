"""Query rewrite prompt templates"""

DEFAULT_QUERY_REWRITE_INSTRUCTIONS = """Use the user information to fill in any vague or ambiguous parts of the query.
Preserve the original intent of the query.
If the query is already clear and unambiguous, leave it unchanged."""

QUERY_REWRITE_TEMPLATE = """# Task
Rewrite the query by clarifying any ambiguous or underspecified references based on the provided user information, making the query more precise.

# User Information
{profile_content}

# Requirements
{custom_instructions}

# Output
Output only the rewritten query—do not add any explanations.

# Query
{query}
"""


def build_query_rewrite_prompt(
    profile_content: str,
    query: str,
    custom_instructions: str = None,
) -> str:
    """
    Build the query rewrite prompt with user profile and query.

    Args:
        profile_content: User profile text
        query: Original query string
        custom_instructions: Optional custom instructions to add to the prompt.
                            If None, uses DEFAULT_QUERY_REWRITE_INSTRUCTIONS.

    Returns:
        Complete prompt string for LLM
    """
    instructions = custom_instructions or DEFAULT_QUERY_REWRITE_INSTRUCTIONS
    return QUERY_REWRITE_TEMPLATE.format(
        profile_content=profile_content,
        query=query,
        custom_instructions=instructions,
    )
