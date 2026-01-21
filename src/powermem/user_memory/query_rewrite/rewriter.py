"""Query rewriter implementation"""

import logging
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

from src.powermem.prompts import build_query_rewrite_prompt

logger = logging.getLogger(__name__)


@dataclass
class QueryRewriteResult:
    """Query rewrite result"""
    original_query: str
    rewritten_query: str
    is_rewritten: bool
    profile_used: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class QueryRewriter:
    """Query rewriter based on user profile"""

    def __init__(
        self,
        llm,
        config: Dict[str, Any],
    ):
        """
        Initialize the rewriter.

        Args:
            llm: LLM instance for generating rewrites
            config: QueryRewriteConfig as dict
        """
        self.llm = llm
        self.config = config
        self.enabled = config.get('enabled', False)
        self.custom_instructions = config.get('prompt')  # 自定义指令（不是完整prompt）

    def rewrite(
        self,
        query: str,
        profile_content: Optional[str] = None
    ) -> QueryRewriteResult:
        """
        Rewrite query based on user profile.

        Args:
            query: Original query string
            profile_content: User profile text from user_profiles table

        Returns:
            QueryRewriteResult: Rewrite result
        """
        # Skip if no user profile
        if not profile_content or not profile_content.strip():
            logger.debug("No profile_content provided, skipping query rewrite")
            return QueryRewriteResult(
                original_query=query,
                rewritten_query=query,
                is_rewritten=False,
            )

        # Skip if query is empty or too short
        if not query or len(query.strip()) < 3:
            logger.debug("Query too short, skipping rewrite")
            return QueryRewriteResult(
                original_query=query,
                rewritten_query=query,
                is_rewritten=False,
            )

        try:
            start_time = time.time()

            # Build prompt
            prompt = build_query_rewrite_prompt(
                profile_content=profile_content,
                query=query,
                custom_instructions=self.custom_instructions,
            )

            # Call LLM for rewrite
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": "You are a helpful query rewriting assistant."},
                    {"role": "user", "content": prompt}
                ]
            )

            rewritten = response.strip()
            elapsed = time.time() - start_time

            # Log rewrite (always log)
            logger.info(f"Query rewrite: '{query}' -> '{rewritten}' (took {elapsed:.2f}s)")

            return QueryRewriteResult(
                original_query=query,
                rewritten_query=rewritten,
                is_rewritten=True,
                profile_used=profile_content,
                metadata={"rewrite_time_seconds": elapsed}
            )

        except Exception as e:
            logger.error(f"Query rewrite failed: {e}, falling back to original query")

            # Fallback to original query on error
            return QueryRewriteResult(
                original_query=query,
                rewritten_query=query,
                is_rewritten=False,
                error=str(e)
            )

