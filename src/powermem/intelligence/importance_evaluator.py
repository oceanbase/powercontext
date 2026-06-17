"""
Importance evaluator for memory content

This module evaluates the importance of memory content using LLM.
"""

import logging
from typing import Any, Dict, Optional
import json
from ..prompts.importance_evaluation import ImportanceEvaluationPrompts

logger = logging.getLogger(__name__)


class ImportanceEvaluator:
    """
    Evaluates the importance of memory content.
    """
    
    def __init__(self, config: Dict[str, Any], llm_config: Dict[str, Any]):
        """
        Initialize importance evaluator.
        
        Args:
            config: Importance evaluator configuration
            llm_config: LLM configuration
        """
        self.config = config
        self.llm_config = llm_config
        self.llm = None  # Will be initialized by the parent manager
        
        # Initialize prompts
        self.prompts = ImportanceEvaluationPrompts(config)
        
        # Importance criteria weights
        self.criteria_weights = {
            "relevance": 0.3,
            "novelty": 0.2,
            "emotional_impact": 0.15,
            "actionable": 0.15,
            "factual": 0.1,
            "personal": 0.1
        }
        
        logger.info("ImportanceEvaluator initialized")
    
    def set_llm(self, llm):
        """
        Set the LLM instance for evaluation.
        
        Args:
            llm: LLM instance
        """
        self.llm = llm
        logger.info("LLM instance set for importance evaluation")
    
    def evaluate_importance(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Evaluate the importance of content.
        
        Args:
            content: Content to evaluate
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Importance score between 0 and 1
        """
        try:
            # Use LLM-based evaluation if available, otherwise fall back to rule-based
            if self.llm:
                importance_score = self._llm_based_evaluation(content, metadata, context)
            else:
                importance_score = self._rule_based_evaluation(content, metadata, context)
            
            logger.debug(f"Evaluated importance: {importance_score}")
            
            return importance_score
            
        except Exception as e:
            logger.error(f"Failed to evaluate importance: {e}")
            return 0.5  # Default medium importance
    
    def _rule_based_evaluation(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Rule-based importance evaluation.
        
        Args:
            content: Content to evaluate
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Importance score between 0 and 1
        """
        score = 0.0
        
        # Length factor
        if len(content) > 100:
            score += 0.1
        elif len(content) > 50:
            score += 0.05
        
        # Keyword importance
        important_keywords = [
            "important", "critical", "urgent", "remember", "note",
            "preference", "like", "dislike", "hate", "love",
            "password", "secret", "private", "confidential"
        ]
        
        content_lower = content.lower()
        for keyword in important_keywords:
            if keyword in content_lower:
                score += 0.1
        
        # Question factor
        if "?" in content:
            score += 0.05
        
        # Exclamation factor
        if "!" in content:
            score += 0.05
        
        # Metadata factors
        if metadata:
            if metadata.get("priority") == "high":
                score += 0.2
            elif metadata.get("priority") == "medium":
                score += 0.1
            
            if metadata.get("tags"):
                score += 0.05
        
        # Context factors
        if context:
            if context.get("user_engagement") == "high":
                score += 0.1
            elif context.get("user_engagement") == "medium":
                score += 0.05
        
        # Cap the score at 1.0
        return min(score, 1.0)
    
    def _llm_based_evaluation(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        LLM-based importance evaluation.
        
        Args:
            content: Content to evaluate
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Importance score between 0 and 1
        """
        if not self.llm:
            logger.warning("LLM not initialized, falling back to rule-based evaluation")
            return self._rule_based_evaluation(content, metadata, context)

        if getattr(self.llm, "is_noop", False) is True:
            logger.info("LLM is disabled; using rule-based importance evaluation.")
            return self._rule_based_evaluation(content, metadata, context)

        try:
            # Prepare evaluation prompt
            prompt = self.prompts.get_importance_evaluation_prompt(content, metadata, context)
            
            # Format prompt as messages for LLM
            messages = [
                {"role": "system", "content": self.prompts.get_system_prompt()},
                {"role": "user", "content": prompt}
            ]
            
            # Call LLM for evaluation
            response = self.llm.generate_response(messages)
            
            # Parse the response to extract importance score
            importance_score = self._parse_importance_response(response)
            
            logger.debug(f"LLM evaluated importance: {importance_score}")
            
            return importance_score
            
        except Exception as e:
            logger.error(f"LLM-based evaluation failed: {e}, falling back to rule-based")
            return self._rule_based_evaluation(content, metadata, context)
    
    def get_importance_breakdown(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, float]:
        """
        Get detailed importance breakdown.
        
        Args:
            content: Content to evaluate
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Dictionary with importance breakdown
        """
        breakdown = {}
        
        for criterion, weight in self.criteria_weights.items():
            # Calculate score for each criterion
            if criterion == "relevance":
                breakdown[criterion] = self._evaluate_relevance(content, context)
            elif criterion == "novelty":
                breakdown[criterion] = self._evaluate_novelty(content, metadata)
            elif criterion == "emotional_impact":
                breakdown[criterion] = self._evaluate_emotional_impact(content)
            elif criterion == "actionable":
                breakdown[criterion] = self._evaluate_actionable(content)
            elif criterion == "factual":
                breakdown[criterion] = self._evaluate_factual(content)
            elif criterion == "personal":
                breakdown[criterion] = self._evaluate_personal(content, metadata)
        
        return breakdown
    
    def _evaluate_relevance(self, content: str, context: Optional[Dict[str, Any]]) -> float:
        """Evaluate relevance of content."""
        # Simple keyword-based relevance
        relevance_keywords = ["relevant", "related", "connected", "associated"]
        content_lower = content.lower()
        
        score = 0.0
        for keyword in relevance_keywords:
            if keyword in content_lower:
                score += 0.25
        
        return min(score, 1.0)
    
    def _evaluate_novelty(self, content: str, metadata: Optional[Dict[str, Any]]) -> float:
        """Evaluate novelty of content."""
        # Check for new information indicators
        novelty_indicators = ["new", "first", "never", "unprecedented", "unique"]
        content_lower = content.lower()
        
        score = 0.0
        for indicator in novelty_indicators:
            if indicator in content_lower:
                score += 0.2
        
        return min(score, 1.0)
    
    def _evaluate_emotional_impact(self, content: str) -> float:
        """Evaluate emotional impact of content."""
        # Check for emotional words
        emotional_words = [
            "happy", "sad", "angry", "excited", "worried", "scared",
            "love", "hate", "fear", "joy", "sorrow", "anger"
        ]
        content_lower = content.lower()
        
        score = 0.0
        for word in emotional_words:
            if word in content_lower:
                score += 0.1
        
        return min(score, 1.0)
    
    def _evaluate_actionable(self, content: str) -> float:
        """Evaluate if content is actionable."""
        # Check for action words
        action_words = [
            "do", "make", "create", "build", "fix", "solve",
            "implement", "execute", "perform", "complete"
        ]
        content_lower = content.lower()
        
        score = 0.0
        for word in action_words:
            if word in content_lower:
                score += 0.1
        
        return min(score, 1.0)
    
    def _evaluate_factual(self, content: str) -> float:
        """Evaluate if content contains factual information."""
        # Check for factual indicators
        factual_indicators = [
            "fact", "data", "statistic", "research", "study",
            "evidence", "proof", "confirmed", "verified"
        ]
        content_lower = content.lower()
        
        score = 0.0
        for indicator in factual_indicators:
            if indicator in content_lower:
                score += 0.15
        
        return min(score, 1.0)
    
    def _parse_importance_response(self, response: str) -> float:
        """
        Parse LLM response to extract importance score.
        
        Args:
            response: LLM response string
            
        Returns:
            Importance score between 0 and 1
        """
        try:
            # Try to extract JSON from response
            if "{" in response and "}" in response:
                start = response.find("{")
                end = response.rfind("}") + 1
                json_str = response[start:end]
                
                result = json.loads(json_str)
                
                if "importance_score" in result:
                    score = float(result["importance_score"])
                    # Ensure score is within valid range
                    return max(0.0, min(1.0, score))
            
            # Fallback: try to extract number from response
            import re
            numbers = re.findall(r'\d+\.?\d*', response)
            if numbers:
                score = float(numbers[0])
                return max(0.0, min(1.0, score))
            
            logger.warning(f"Could not parse importance score from response: {response}")
            return 0.5  # Default medium importance
            
        except Exception as e:
            logger.error(f"Failed to parse importance response: {e}")
            return 0.5  # Default medium importance
    
    def _evaluate_personal(self, content: str, metadata: Optional[Dict[str, Any]]) -> float:
        """Evaluate if content is personal."""
        # Check for personal indicators
        personal_indicators = [
            "i", "me", "my", "mine", "myself",
            "personal", "private", "confidential"
        ]
        content_lower = content.lower()
        
        score = 0.0
        for indicator in personal_indicators:
            if indicator in content_lower:
                score += 0.1
        
        return min(score, 1.0)
