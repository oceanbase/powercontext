"""Abstract base class for skill storage."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class SkillStoreBase(ABC):
    """Abstract interface for skill storage backends."""

    @abstractmethod
    def create_table(self) -> None:
        """Create the skills table if it doesn't exist."""

    @abstractmethod
    def add(
        self,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        procedure_data: Optional[Dict[str, Any]] = None,
        title_embedding: Optional[List[float]] = None,
        description_embedding: Optional[List[float]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a skill. Returns {"id": int, ...}."""

    @abstractmethod
    def update(
        self,
        skill_id: int,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        procedure_data: Optional[Dict[str, Any]] = None,
        title_embedding: Optional[List[float]] = None,
        description_embedding: Optional[List[float]] = None,
    ) -> bool:
        """Update an existing skill. Returns True on success."""

    @abstractmethod
    def get(self, skill_id: int) -> Optional[Dict[str, Any]]:
        """Get a single skill by ID."""

    @abstractmethod
    def search(
        self,
        query_embedding: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        limit: int = 10,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search skills by embedding and/or fulltext. Returns list with scores."""

    @abstractmethod
    def update_status(self, skill_id: int, status: str) -> bool:
        """Update the status of a skill. Returns True if the row was changed."""

    @abstractmethod
    def delete(self, skill_id: int) -> bool:
        """Delete a skill by ID."""
