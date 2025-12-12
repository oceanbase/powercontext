"""
Abstract base class for user profile storage implementations

This module defines the user profile storage interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class UserProfileStoreBase(ABC):
    """
    Abstract base class for user profile storage implementations.
    
    This class defines the interface that all user profile storage backends must implement.
    """

    @abstractmethod
    def save_profile(
        self,
        user_id: str,
        profile_content: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> int:
        """
        Save or update user profile based on unique combination of user_id, agent_id, run_id.
        If a record exists with the same combination, update it; otherwise, insert a new record.

        Args:
            user_id: User identifier
            profile_content: Profile content text
            agent_id: Optional agent identifier
            run_id: Optional run identifier

        Returns:
            Profile ID (existing or newly generated Snowflake ID)
        """
        pass

    @abstractmethod
    def get_profile(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get user profile by user_id and optional filters.

        Args:
            user_id: User identifier
            agent_id: Optional agent identifier for filtering
            run_id: Optional run identifier for filtering

        Returns:
            Profile dictionary with id, user_id, profile_content, created_at, updated_at, etc., or None if not found
        """
        pass

    @abstractmethod
    def delete_profile(self, profile_id: int) -> bool:
        """
        Delete user profile by profile_id.

        Args:
            profile_id: Profile ID (Snowflake ID)

        Returns:
            True if deleted, False if not found
        """
        pass