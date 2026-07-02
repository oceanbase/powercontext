"""
Ebbinghaus forgetting curve algorithm

This module implements the Ebbinghaus forgetting curve for memory management.
"""

import logging
import math
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timedelta
from powermem.utils.utils import get_current_datetime

logger = logging.getLogger(__name__)


class EbbinghausAlgorithm:
    """
    Implements Ebbinghaus forgetting curve algorithm for memory management.
    """

    DEFAULT_DECAY_RATE_MULTIPLIERS = {
        "working": 1,
        "short_term": 7,
        "long_term": 60,
    }

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Ebbinghaus algorithm.

        Args:
            config: Algorithm configuration
        """
        self.config = config

        # Ebbinghaus curve parameters
        self.initial_retention = config.get("initial_retention", 1.0)
        self.decay_rate = config.get("decay_rate", 1.5)
        self.decay_rate_multipliers = self._load_decay_rate_multipliers(
            config.get("decay_rate_multipliers")
        )
        self.reinforcement_factor = config.get("reinforcement_factor", 0.3)

        # Memory type thresholds
        self.working_threshold = config.get("working_threshold", 0.3)
        self.short_term_threshold = config.get("short_term_threshold", 0.6)
        self.long_term_threshold = config.get("long_term_threshold", 0.8)

        # Time intervals (in hours)
        self.review_intervals = config.get("review_intervals", [1, 6, 24, 72, 168])
        # Review schedule: higher importance -> shorter intervals (shared by persist + query)
        self.review_adjustment_factor = config.get("review_adjustment_factor", 0.3)
        self.review_interval_min_hours = config.get("review_interval_min_hours", 0.5)

        logger.info("EbbinghausAlgorithm initialized")

    def process_memory_metadata(
        self, content: str, importance_score: float, memory_type: str
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

            initial_retention = self._calculate_initial_retention(importance_score)

            # Calculate decay rate based on memory type
            decay_rate = self._get_decay_rate_for_type(memory_type)

            # Generate review schedule
            review_schedule = self._generate_review_schedule(
                importance_score, current_time
            )

            # Calculate next review time
            next_review = (
                review_schedule[0]
                if review_schedule
                else current_time + timedelta(hours=1)
            )

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

            logger.debug(
                f"Generated intelligence metadata for type: {memory_type}, importance: {importance_score}"
            )

            return intelligence_metadata

        except Exception as e:
            logger.error(f"Failed to process memory metadata: {e}")
            return {
                "intelligence": {
                    "importance_score": importance_score,
                    "memory_type": memory_type,
                    "error": str(e),
                }
            }

    def calculate_decay(
        self,
        created_at,
        decay_rate: Optional[float] = None,
    ) -> float:
        """
        Calculate decay factor based on time elapsed.

        Args:
            created_at: When the memory was created (datetime object or ISO string)
            decay_rate: Per-memory strength parameter. Larger values decay slower.

        Returns:
            Decay factor between 0 and 1
        """
        try:
            # Handle both datetime objects and ISO string formats
            if isinstance(created_at, str):
                if created_at:
                    created_at = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                else:
                    # If empty string, use current time
                    created_at = get_current_datetime()
            elif created_at is None:
                # If None, use current time
                created_at = get_current_datetime()

            time_elapsed = get_current_datetime() - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600

            rate = self.decay_rate if decay_rate is None else decay_rate
            if rate <= 0:
                logger.warning("Invalid decay_rate %s, falling back to default", rate)
                rate = self.decay_rate

            # Ebbinghaus forgetting curve: R = e^(-t/S)
            # where R is retention, t is time, S is strength.
            decay_factor = math.exp(-hours_elapsed / (24 * rate))

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
            content = (memory.get("content") or memory.get("memory") or "").lower()
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

            # Check recency — only promote if the memory has been accessed at
            # least once, so that old never-accessed memories can still be forgotten.
            created_at = memory.get("created_at")
            if created_at and access_count > 0:
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
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
            if self.calculate_current_retention(memory) < self.working_threshold:
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
                    created_at = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
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

    def reinforce(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        """Boost current_retention on review and advance the review schedule.

        Called when a memory is accessed at or after its ``next_review`` time.
        Uses diminishing-returns formula so retention approaches but never
        exceeds 1.0.

        Returns:
            Dict with updated intelligence fields to merge back.
        """
        _, intelligence = self._resolve_metadata_sections(memory)
        current_retention = self.calculate_current_retention(memory)
        reinforcement_factor = self._resolve_reinforcement_factor(memory)
        review_count = int(intelligence.get("review_count") or 0)

        new_retention = min(
            1.0, current_retention + reinforcement_factor * (1.0 - current_retention)
        )
        new_review_count = review_count + 1

        review_schedule = intelligence.get("review_schedule") or []
        next_review = (
            review_schedule[new_review_count]
            if new_review_count < len(review_schedule)
            else None
        )

        now = get_current_datetime()
        return {
            "current_retention": new_retention,
            "review_count": new_review_count,
            "last_reviewed": now.isoformat(),
            "next_review": next_review,
        }

    def calculate_current_retention(self, memory: Dict[str, Any]) -> float:
        """Return the real-time effective retention for display/ranking.

        ``current_retention`` is a snapshot captured at ``last_reviewed`` (or
        creation time for initial metadata), so it must decay before runtime
        consumers use it. This avoids treating initialized retention as a
        permanent floor while still reflecting recent review reinforcement.
        """
        stored = self._resolve_current_retention(memory)
        if stored is not None:
            anchor = self._resolve_retention_anchor(memory)
            decay = self.calculate_decay(
                anchor,
                decay_rate=self._resolve_decay_rate(memory),
            )
            return max(0.0, min(1.0, stored * decay))

        initial = self._resolve_initial_retention(memory)
        created_at = self._resolve_created_at(memory)
        decay = self.calculate_decay(
            created_at,
            decay_rate=self._resolve_decay_rate(memory),
        )
        return max(0.0, min(1.0, initial * decay))

    def get_review_schedule(
        self, memory: Dict[str, Any], *, prefer_stored: bool = True
    ) -> list:
        """
        Get review schedule for a memory.

        When ``prefer_stored`` is True (default), returns persisted
        ``metadata.intelligence.review_schedule`` if present so query results
        match the database. Otherwise recomputes using the same formula as
        ``_generate_review_schedule()`` (via ``_build_review_schedule``).

        Args:
            memory: Memory data (top-level or metadata fields)
            prefer_stored: If True, use persisted ISO timestamps when available

        Returns:
            List of review times
        """
        try:
            if prefer_stored:
                stored = self._parse_stored_review_schedule(memory)
                if stored:
                    return stored
            importance, created_at = self._resolve_review_schedule_inputs(memory)
            return self._build_review_schedule(importance, created_at)
        except Exception as e:
            logger.error(f"Failed to get review schedule: {e}")
            return []

    def _load_decay_rate_multipliers(
        self, raw: Optional[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Load per-memory-type decay multipliers with safe defaults."""
        multipliers = self.DEFAULT_DECAY_RATE_MULTIPLIERS.copy()
        if raw:
            for memory_type, value in raw.items():
                try:
                    multiplier = float(value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid decay multiplier for %s: %s", memory_type, value
                    )
                    continue
                if multiplier > 0:
                    multipliers[memory_type] = multiplier
        self._validate_decay_rate_multipliers(multipliers)
        return multipliers

    def _validate_decay_rate_multipliers(self, multipliers: Dict[str, float]) -> None:
        """Warn when default tier ordering would not make higher tiers last longer."""
        working = multipliers.get("working", self.decay_rate)
        short_term = multipliers.get("short_term", self.decay_rate)
        long_term = multipliers.get("long_term", self.decay_rate)
        if not working < short_term < long_term:
            logger.warning(
                "decay_rate_multipliers should satisfy "
                "working < short_term < long_term"
            )

    def _get_decay_rate_for_type(self, memory_type: str) -> float:
        """Get decay strength S based on memory type; larger S decays slower."""
        multiplier = self.decay_rate_multipliers.get(memory_type)
        if multiplier is None:
            return self.decay_rate
        return self.decay_rate * multiplier

    def _resolve_decay_rate(self, memory: Dict[str, Any]) -> float:
        """Resolve the effective decay strength for a memory dict."""
        meta, intelligence = self._resolve_metadata_sections(memory)

        memory_type = (
            memory.get("memory_type")
            or meta.get("memory_type")
            or intelligence.get("memory_type")
        )
        if memory_type:
            base_rate = self._get_decay_rate_for_type(memory_type)
            return self._apply_reinforcement(memory, base_rate)

        stored = self._first_present(
            memory.get("decay_rate"),
            meta.get("decay_rate"),
            intelligence.get("decay_rate"),
        )
        if stored is not None:
            try:
                rate = float(stored)
            except (TypeError, ValueError):
                logger.warning("Invalid stored decay_rate: %s", stored)
            else:
                if rate > 0:
                    return self._apply_reinforcement(memory, rate)
        return self._apply_reinforcement(memory, self.decay_rate)

    def _resolve_metadata_sections(
        self, memory: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return metadata and intelligence sections from supported layouts."""
        meta = memory.get("metadata") or {}
        intelligence = meta.get("intelligence") or memory.get("intelligence") or {}
        return meta, intelligence

    @staticmethod
    def _first_present(*values: Any) -> Any:
        """Return the first value that is not None."""
        for value in values:
            if value is not None:
                return value
        return None

    def _apply_reinforcement(self, memory: Dict[str, Any], base_rate: float) -> float:
        """Increase decay strength with diminishing returns on access frequency."""
        access_count = self._resolve_access_count(memory)
        reinforcement_factor = self._resolve_reinforcement_factor(memory)
        return base_rate * (1 + reinforcement_factor * math.log1p(access_count))

    def _resolve_access_count(self, memory: Dict[str, Any]) -> int:
        """Resolve access_count from top-level, metadata, or intelligence."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        raw = self._first_present(
            memory.get("access_count"),
            meta.get("access_count"),
            intelligence.get("access_count"),
        )
        try:
            access_count = int(raw or 0)
        except (TypeError, ValueError):
            logger.warning("Invalid access_count for reinforcement: %s", raw)
            return 0
        return max(access_count, 0)

    def _resolve_reinforcement_factor(self, memory: Dict[str, Any]) -> float:
        """Resolve per-memory reinforcement factor with config fallback."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        raw = self._first_present(
            memory.get("reinforcement_factor"),
            meta.get("reinforcement_factor"),
            intelligence.get("reinforcement_factor"),
            self.reinforcement_factor,
        )
        try:
            factor = float(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid reinforcement_factor: %s", raw)
            return 0.0
        return max(factor, 0.0)

    def _resolve_initial_retention(self, memory: Dict[str, Any]) -> float:
        """Resolve initial_retention from memory metadata with config fallback."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        raw = self._first_present(
            memory.get("initial_retention"),
            meta.get("initial_retention"),
            intelligence.get("initial_retention"),
        )
        if raw is not None:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                logger.warning("Invalid initial_retention: %s", raw)
            else:
                if 0.0 < val <= 1.0:
                    return val
        return self.initial_retention

    def _calculate_initial_retention(self, importance_score: float) -> float:
        """Calculate bounded initial retention for newly persisted memories."""
        try:
            score = float(importance_score)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid importance_score for retention: %s", importance_score
            )
            score = 0.0

        score = max(0.0, min(1.0, score))
        max_retention = max(0.0, min(1.0, float(self.initial_retention)))
        retention_floor = min(max_retention, max(0.0, min(1.0, self.working_threshold)))
        return max(max_retention * score, retention_floor)

    def _resolve_current_retention(self, memory: Dict[str, Any]) -> Optional[float]:
        """Resolve stored current_retention as a bounded snapshot value."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        raw = self._first_present(
            memory.get("current_retention"),
            meta.get("current_retention"),
            intelligence.get("current_retention"),
        )
        if raw is None:
            return None
        try:
            retention = float(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid current_retention: %s", raw)
            return None
        return max(0.0, min(1.0, retention))

    def _resolve_created_at(self, memory: Dict[str, Any]) -> Any:
        """Resolve creation timestamp from supported memory layouts."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        return self._first_present(
            memory.get("created_at"),
            meta.get("created_at"),
            intelligence.get("created_at"),
        )

    def _resolve_retention_anchor(self, memory: Dict[str, Any]) -> Any:
        """Resolve the timestamp for decaying current_retention snapshots."""
        meta, intelligence = self._resolve_metadata_sections(memory)
        return self._first_present(
            intelligence.get("last_reviewed"),
            memory.get("last_reviewed"),
            meta.get("last_reviewed"),
            self._resolve_created_at(memory),
        )

    def _parse_datetime(self, value: Any) -> datetime:
        """Parse datetime from object or ISO string."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return get_current_datetime()

    def _parse_stored_review_schedule(
        self, memory: Dict[str, Any]
    ) -> Optional[List[datetime]]:
        """Parse persisted review_schedule ISO strings from memory metadata."""
        meta = memory.get("metadata") or {}
        intelligence = meta.get("intelligence") or memory.get("intelligence") or {}
        raw = intelligence.get("review_schedule")
        if not raw or not isinstance(raw, list):
            return None
        times: List[datetime] = []
        for item in raw:
            if not item:
                continue
            times.append(self._parse_datetime(item))
        return times if times else None

    def _resolve_review_schedule_inputs(
        self, memory: Dict[str, Any]
    ) -> tuple[float, datetime]:
        """Resolve importance and created_at from a memory dict (incl. metadata)."""
        meta = memory.get("metadata") or {}
        intelligence = meta.get("intelligence") or memory.get("intelligence") or {}

        importance = memory.get("importance_score")
        if importance is None:
            importance = meta.get("importance_score")
        if importance is None:
            importance = intelligence.get("importance_score", 0.5)

        created_at = memory.get("created_at")
        if created_at is None:
            created_at = meta.get("created_at")
        if created_at is None:
            created_at = intelligence.get("created_at", get_current_datetime())

        return float(importance), self._parse_datetime(created_at)

    def _adjust_review_intervals(self, importance_score: float) -> List[float]:
        """Scale base review intervals by importance (higher -> shorter)."""
        factor = self.review_adjustment_factor
        floor = self.review_interval_min_hours
        adjusted = []
        for interval in self.review_intervals:
            hours = interval * (1 - importance_score * factor)
            adjusted.append(max(hours, floor))
        return adjusted

    def _build_review_schedule(
        self, importance_score: float, created_at: datetime
    ) -> List[datetime]:
        """Build review datetimes from importance and anchor time."""
        intervals = self._adjust_review_intervals(importance_score)
        return [created_at + timedelta(hours=hours) for hours in intervals]

    def _generate_review_schedule(
        self, importance_score: float, created_at: datetime
    ) -> List[datetime]:
        """Generate review schedule based on importance and Ebbinghaus curve."""
        try:
            return self._build_review_schedule(importance_score, created_at)
        except Exception as e:
            logger.error(f"Failed to generate review schedule: {e}")
            return []
