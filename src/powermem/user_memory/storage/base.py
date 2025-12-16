"""
Abstract base class for user profile storage implementations

This module defines the user profile storage interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List


class UserProfileStoreBase(ABC):
    """
    Abstract base class for user profile storage implementations.
    
    This class defines the interface that all user profile storage backends must implement.
    """

    @abstractmethod
    def save_profile(
        self,
        user_id: str,
        profile_content: Optional[str] = None,
        topics: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Save or update user profile based on unique combination of user_id, agent_id, run_id.
        If a record exists with the same combination, update it; otherwise, insert a new record.

        Args:
            user_id: User identifier
            profile_content: Profile content text (for non-structured profile)
            topics: Structured topics dictionary (for structured profile)

        Returns:
            Profile ID (existing or newly generated Snowflake ID)
        """
        pass

    @abstractmethod
    def get_profile_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user profile by user_id only, returning the unique record.

        Args:
            user_id: User identifier (required)

        Returns:
            Profile dictionary with id, user_id, profile_content, created_at, updated_at, etc., or None if not found
        """
        pass

    @abstractmethod
    def get_profile(
        self,
        user_id: Optional[str] = None,
        main_topic: Optional[List[str]] = None,
        sub_topic: Optional[List[str]] = None,
        topic_value: Optional[List[str]] = None,
        limit: Optional[int] = 100,
        offset: Optional[int] = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get user profiles by user_id and optional filters.

        Args:
            user_id: Optional User identifier
            main_topic: Optional list of main topic names to filter by (SQL-level filtering)
            sub_topic: Optional list of sub topic names to filter by (SQL-level filtering)
            topic_value: Optional list of topic values to filter by exact match
            limit: Optional limit on the number of profiles to return (default: 100)
            offset: Optional offset for pagination

        Returns:
            List of profile dictionaries, each with id, user_id, profile_content, created_at, updated_at, etc.
            Returns empty list if no profiles found
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