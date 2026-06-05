"""Abstract base class for source storage (fact-source linking)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class SourceStoreBase(ABC):
    """Abstract interface for source storage backends.

    A *source* represents the origin of one or more downstream records --
    for example a conversation turn, a file upload, or an API call.
    Each memory / skill record can optionally reference the
    source it was extracted from via a dedicated link table.
    """

    @abstractmethod
    def create_table(self) -> None:
        """Create the sources + link tables if they do not exist."""

    @abstractmethod
    def create_source(
        self,
        source_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a source record.

        The four scope columns (``user_id`` / ``agent_id`` / ``run_id`` /
        ``actor_id``) mirror the scope dimensions on the main memory table
        so that sources can be queried / purged along the same axes as the
        records they spawn.

        Returns:
            ``{"id": <int>, "source_type": ..., ...}``
        """

    @abstractmethod
    def get_source(self, source_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a source by its primary key."""

    # ------------------------------------------------------------------ #
    # Memory linking
    # ------------------------------------------------------------------ #

    @abstractmethod
    def link_memory(self, source_id: int, memory_id: int) -> bool:
        """Create a link between a source and a memory record.

        The write itself is idempotent (the underlying unique key guarantees
        at most one row per ``(source_id, memory_id)`` pair), but the return
        value carries whether this particular call actually created the link:

        - ``True``  -- a new link row was inserted by this call.
        - ``False`` -- the link already existed; nothing was inserted.
        """

    @abstractmethod
    def unlink_memory(self, source_id: int, memory_id: int) -> bool:
        """Remove a link between a source and a memory record.

        Returns ``True`` if a link was actually removed.
        """

    @abstractmethod
    def get_sources_for_memory(self, memory_id: int) -> List[Dict[str, Any]]:
        """Return all sources linked to a given memory record."""

    # ------------------------------------------------------------------ #
    # Skill linking
    # ------------------------------------------------------------------ #

    @abstractmethod
    def link_skill(self, source_id: int, skill_id: int) -> bool:
        """Create a link between a source and a skill record."""

    @abstractmethod
    def unlink_skill(self, source_id: int, skill_id: int) -> bool:
        """Remove a link between a source and a skill record."""

    @abstractmethod
    def get_sources_for_skill(self, skill_id: int) -> List[Dict[str, Any]]:
        """Return all sources linked to a given skill record."""

    # ------------------------------------------------------------------ #
    # Reverse queries
    # ------------------------------------------------------------------ #

    @abstractmethod
    def get_memories_for_source(self, source_id: int) -> List[int]:
        """Return all memory IDs linked to a given source."""

    @abstractmethod
    def get_skills_for_source(self, source_id: int) -> List[int]:
        """Return all skill IDs linked to a given source."""

    @abstractmethod
    def delete_source(self, source_id: int) -> bool:
        """Delete a source and all its memory/skill links."""
