"""
Ebbinghaus forgetting curve algorithm

This module implements the Ebbinghaus forgetting curve for memory management.
"""

import logging
import math
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta
from powermem.utils.utils import get_current_datetime

logger = logging.getLogger(__name__)


class EbbinghausAlgorithm:
    """
    Implements Ebbinghaus forgetting curve algorithm for memory management.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Ebbinghaus algorithm.
        
        Args:
            config: Algorithm configuration
        """
        self.config = config
        
        # Ebbinghaus curve parameters
        self.initial_retention = config.get("initial_retention", 1.0)
        self.decay_rate = config.get("decay_rate", 0.1)
        self.reinforcement_factor = config.get("reinforcement_factor", 0.3)
        
        # Memory type thresholds
        self.working_threshold = config.get("working_threshold", 0.3)
        self.short_term_threshold = config.get("short_term_threshold", 0.6)
        self.long_term_threshold = config.get("long_term_threshold", 0.8)
        
        # Time intervals (in hours)
        self.review_intervals = config.get("review_intervals", [1, 6, 24, 72, 168])
        
        logger.info("EbbinghausAlgorithm initialized")
    
    def process_memory_metadata(
        self,
        content: str,
        importance_score: float,
        memory_type: str
    ) -> Dict[str, Any]:
        """
        Process memory using Ebbinghaus algorithm and return metadata.
        
        Args:
            content: Memory content
            importance_score: Importance score
            memory_type: Type of memory
            
        Returns:
            Dictionary containing intelligence metadata
        """
        try:
            current_time = get_current_datetime()
            
            # Calculate initial retention based on importance
            initial_retention = self.initial_retention * importance_score
            
            # Calculate decay rate based on memory type
            decay_rate = self._get_decay_rate_for_type(memory_type)
            
            # Generate review schedule
            review_schedule = self._generate_review_schedule(importance_score, current_time)
            
            # Calculate next review time
            next_review = review_schedule[0] if review_schedule else current_time + timedelta(hours=1)
            
            intelligence_metadata = {
                # Ebbinghaus algorithm data
                "intelligence": {
                    "importance_score": importance_score,
                    "memory_type": memory_type,
                    "initial_retention": initial_retention,
                    "decay_rate": decay_rate,
                    "current_retention": initial_retention,
                    "next_review": next_review.isoformat(),
                    "review_schedule": [rt.isoformat() for rt in review_schedule],
                    "last_reviewed": current_time.isoformat(),
                    "review_count": 0,
                    "access_count": 0,
                    "reinforcement_factor": self.reinforcement_factor,
                },
                # Memory management flags
                "memory_management": {
                    "should_promote": False,
                    "should_forget": False,
                    "should_archive": False,
                    "is_active": True,
                },
                # Timestamps
                "created_at": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
            }
            
            logger.debug(f"Generated intelligence metadata for type: {memory_type}, importance: {importance_score}")
            
            return intelligence_metadata
            
        except Exception as e:
            logger.error(f"Failed to process memory metadata: {e}")
            return {
                "intelligence": {
                    "importance_score": importance_score,
                    "memory_type": memory_type,
                    "error": str(e)
                }
            }
    
    def calculate_decay(self, created_at) -> float:
        """
        Calculate decay factor based on time elapsed.
        
        Args:
            created_at: When the memory was created (datetime object or ISO string)
            
        Returns:
            Decay factor between 0 and 1
        """
        try:
            # Handle both datetime objects and ISO string formats
            if isinstance(created_at, str):
                if created_at:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    # If empty string, use current time
                    created_at = get_current_datetime()
            elif created_at is None:
                # If None, use current time
                created_at = get_current_datetime()
            
            time_elapsed = get_current_datetime() - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            
            # Ebbinghaus forgetting curve: R = e^(-t/S)
            # where R is retention, t is time, S is strength
            decay_factor = math.exp(-hours_elapsed / (24 * self.decay_rate))
            
            return max(decay_factor, 0.0)
            
        except Exception as e:
            logger.error(f"Failed to calculate decay: {e}")
            return 0.5
    
    def calculate_relevance(self, memory: Dict[str, Any], query: str) -> float:
        """
        Calculate relevance score for a memory given a query.
        
        Args:
            memory: Memory data
            query: Search query
            
        Returns:
            Relevance score between 0 and 1
        """
        try:
            content = memory.get("content", "").lower()
            query_lower = query.lower()
            
            # Simple keyword matching
            query_words = query_lower.split()
            content_words = content.split()
            
            matches = 0
            for word in query_words:
                if word in content_words:
                    matches += 1
            
            relevance_score = matches / len(query_words) if query_words else 0.0
            
            return min(relevance_score, 1.0)
            
        except Exception as e:
            logger.error(f"Failed to calculate relevance: {e}")
            return 0.0
    
    def should_promote(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be promoted to a higher tier.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be promoted
        """
        try:
            # Check access frequency
            access_count = memory.get("access_count", 0)
            if access_count >= 3:
                return True
            
            # Check recency
            created_at = memory.get("created_at")
            if created_at:
                # Parse string to datetime if needed
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                time_elapsed = get_current_datetime() - created_at
                if time_elapsed > timedelta(hours=24):
                    return True
            
            # Check importance
            importance = memory.get("importance_score", 0.5)
            if importance >= self.short_term_threshold:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check promotion: {e}")
            return False
    
    def should_forget(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be forgotten.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be forgotten
        """
        try:
            # Check decay factor
            created_at = memory.get("created_at")
            if created_at:
                decay_factor = self.calculate_decay(created_at)
                if decay_factor < self.working_threshold:
                    return True
            
            # Check access frequency
            access_count = memory.get("access_count", 0)
            if access_count == 0:
                # Check if memory is old enough to be forgotten
                if created_at:
                    # Parse string to datetime if needed
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    time_elapsed = get_current_datetime() - created_at
                    if time_elapsed > timedelta(days=7):
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check forgetting: {e}")
            return False
    
    def should_archive(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be archived.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be archived
        """
        try:
            # Check age
            created_at = memory.get("created_at")
            if created_at:
                # Parse string to datetime if needed
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                time_elapsed = get_current_datetime() - created_at
                if time_elapsed > timedelta(days=30):
                    return True
            
            # Check importance
            importance = memory.get("importance_score", 0.5)
            if importance < self.working_threshold:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check archiving: {e}")
            return False
    
    def get_review_schedule(self, memory: Dict[str, Any]) -> list:
        """
        Get review schedule for a memory based on Ebbinghaus curve.
        
        Args:
            memory: Memory data
            
        Returns:
            List of review times
        """
        try:
            created_at = memory.get("created_at", get_current_datetime())
            # Parse string to datetime if needed
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            importance = memory.get("importance_score", 0.5)
            
            # Adjust intervals based on importance
            adjusted_intervals = []
            for interval in self.review_intervals:
                # Higher importance = shorter intervals
                adjusted_interval = interval * (1 - importance * 0.5)
                adjusted_intervals.append(adjusted_interval)
            
            # Calculate review times
            review_times = []
            for interval in adjusted_intervals:
                review_time = created_at + timedelta(hours=interval)
                review_times.append(review_time)
            
            return review_times
            
        except Exception as e:
            logger.error(f"Failed to get review schedule: {e}")
            return []
    
    def _get_decay_rate_for_type(self, memory_type: str) -> float:
        """Get decay rate based on memory type."""
        decay_rates = {
            "working": self.decay_rate * 2.0,  # Faster decay for working memory
            "short_term": self.decay_rate * 1.5,  # Medium decay for short-term
            "long_term": self.decay_rate,  # Standard decay for long-term
        }
        return decay_rates.get(memory_type, self.decay_rate)
    
    def _generate_review_schedule(self, importance_score: float, created_at: datetime) -> List[datetime]:
        """Generate review schedule based on importance and Ebbinghaus curve."""
        try:
            # Adjust intervals based on importance
            adjusted_intervals = []
            for interval in self.review_intervals:
                # Higher importance = shorter intervals (more frequent reviews)
                adjusted_interval = interval * (1 - importance_score * 0.3)
                adjusted_intervals.append(max(adjusted_interval, 0.5))  # Minimum 0.5 hours
            
            # Calculate review times
            review_times = []
            for interval in adjusted_intervals:
                review_time = created_at + timedelta(hours=interval)
                review_times.append(review_time)
            
            return review_times
            
        except Exception as e:
            logger.error(f"Failed to generate review schedule: {e}")
            return []
