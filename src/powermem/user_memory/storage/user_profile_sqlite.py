"""
User Profile storage implementation for SQLite

This module provides storage for user profile information extracted from conversations.
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Optional, Dict, Any, List

from ...utils.utils import serialize_datetime, generate_snowflake_id, get_current_datetime

from .base import UserProfileStoreBase

logger = logging.getLogger(__name__)


class SQLiteUserProfileStore(UserProfileStoreBase):
    """SQLite-based user profile storage implementation"""

    def __init__(
            self,
            table_name: str = "user_profiles",
            database_path: str = ":memory:",
            **kwargs,
    ):
        """
        Initialize the UserProfileStore.

        Args:
            table_name (str): Name of the table to store user profiles.
            database_path (str): Path to SQLite database file. Use ":memory:" for in-memory database.
        """
        self.table_name = table_name
        self.primary_field = "id"
        self.db_path = database_path
        self.connection = None
        self._lock = threading.Lock()

        # Create directory if database path is not in-memory and directory doesn't exist
        if database_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(database_path))
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir, exist_ok=True)
                    logger.info(f"Created database directory: {db_dir}")
                except OSError as e:
                    logger.error(f"Failed to create database directory {db_dir}: {e}")
                    raise

        # Connect to database
        try:
            self.connection = sqlite3.connect(database_path, check_same_thread=False)
        except Exception as e:
            logger.error(f"Failed to connect to SQLite database at {database_path}: {e}")
            raise

        # Create table if it doesn't exist
        self._create_table()

        logger.info(f"SQLiteUserProfileStore initialized with db_path: {database_path}")

    def _create_table(self) -> None:
        """Create user profiles table if it doesn't exist."""
        with self._lock:
            cursor = self.connection.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (self.table_name,)
            )
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                # Create table
                create_sql = f"""
                    CREATE TABLE {self.table_name} (
                        {self.primary_field} INTEGER PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        profile_content TEXT,
                        topics TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """
                cursor.execute(create_sql)

                # Create index on user_id
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_user_id ON {self.table_name} (user_id)"
                )

                self.connection.commit()
                logger.info(f"Created user profiles table: {self.table_name}")
            else:
                logger.info(f"User profiles table '{self.table_name}' already exists")

    def save_profile(
            self,
            user_id: str,
            profile_content: Optional[str] = None,
            topics: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Save or update user profile based on unique combination of user_id.
        If a record exists with the same combination, update it; otherwise, insert a new record.

        Args:
            user_id: User identifier
            profile_content: Profile content text (for non-structured profile)
            topics: Structured topics dictionary (for structured profile)

        Returns:
            Profile ID (existing or newly generated Snowflake ID)
        """
        now = serialize_datetime(get_current_datetime())

        with self._lock:
            cursor = self.connection.cursor()

            # Check if profile exists with the same user_id
            cursor.execute(
                f"SELECT {self.primary_field} FROM {self.table_name} WHERE user_id = ? LIMIT 1",
                (user_id,)
            )
            existing_row = cursor.fetchone()

            if existing_row:
                # Update existing record
                profile_id = existing_row[0]

                update_fields = ["updated_at = ?"]
                update_values = [now]

                if profile_content is not None:
                    update_fields.append("profile_content = ?")
                    update_values.append(profile_content)

                if topics is not None:
                    update_fields.append("topics = ?")
                    update_values.append(json.dumps(topics, ensure_ascii=False))

                update_values.append(profile_id)

                update_sql = f"""
                    UPDATE {self.table_name}
                    SET {', '.join(update_fields)}
                    WHERE {self.primary_field} = ?
                """
                cursor.execute(update_sql, update_values)
                self.connection.commit()
                logger.debug(f"Updated profile for user_id: {user_id}, profile_id: {profile_id}")
            else:
                # Insert new record
                profile_id = generate_snowflake_id()

                topics_json = json.dumps(topics, ensure_ascii=False) if topics is not None else None

                insert_sql = f"""
                    INSERT INTO {self.table_name}
                    ({self.primary_field}, user_id, profile_content, topics, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """
                cursor.execute(insert_sql, (
                    profile_id,
                    user_id,
                    profile_content,
                    topics_json,
                    now,
                    now
                ))
                self.connection.commit()
                logger.debug(f"Created profile for user_id: {user_id}, profile_id: {profile_id}")

        return profile_id

    def get_profile_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user profile by user_id only, returning the unique record.

        Args:
            user_id: User identifier (required)

        Returns:
            Profile dictionary with the following keys:
            - "id" (int): Profile ID
            - "user_id" (str): User identifier
            - "profile_content" (str): Profile content text
            - "topics" (dict): Structured topics dictionary
            - "created_at" (str): Creation timestamp in ISO format
            - "updated_at" (str): Last update timestamp in ISO format
            or None if not found
        """
        with self._lock:
            cursor = self.connection.cursor()

            cursor.execute(
                f"""
                SELECT {self.primary_field}, user_id, profile_content, topics, created_at, updated_at
                FROM {self.table_name}
                WHERE user_id = ?
                ORDER BY {self.primary_field} DESC
                LIMIT 1
                """,
                (user_id,)
            )
            row = cursor.fetchone()

            if row:
                topics = None
                if row[3]:
                    try:
                        topics = json.loads(row[3])
                    except json.JSONDecodeError:
                        topics = None

                return {
                    "id": row[0],
                    "user_id": row[1],
                    "profile_content": row[2],
                    "topics": topics,
                    "created_at": row[4],
                    "updated_at": row[5],
                }
            return None

    def _check_json_path_exists(self, topics: Dict[str, Any], json_path: str) -> bool:
        """
        Check if a JSON path exists in topics dictionary.

        Args:
            topics: Topics dictionary
            json_path: JSON path in format "$.main_topic" or "$.main_topic.sub_topic"

        Returns:
            True if path exists, False otherwise
        """
        if not topics or not isinstance(topics, dict):
            return False

        # Remove leading "$." if present
        if json_path.startswith("$."):
            json_path = json_path[2:]

        parts = json_path.split(".")
        current = topics

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False

        return True

    def _check_topic_value_exists(self, topics: Dict[str, Any], value: str) -> bool:
        """
        Check if a value exists anywhere in the topics dictionary.

        Args:
            topics: Topics dictionary
            value: Value to search for

        Returns:
            True if value exists, False otherwise
        """
        if not topics:
            return False

        def search_value(obj: Any) -> bool:
            if isinstance(obj, dict):
                for v in obj.values():
                    if search_value(v):
                        return True
            elif isinstance(obj, list):
                for item in obj:
                    if search_value(item):
                        return True
            elif isinstance(obj, str):
                return obj == value
            else:
                return str(obj) == value
            return False

        return search_value(topics)

    def _matches_filters(
            self,
            topics: Optional[Dict[str, Any]],
            main_topic: Optional[List[str]],
            sub_topic: Optional[List[str]],
            topic_value: Optional[List[str]],
    ) -> bool:
        """
        Check if topics match the given filters.

        Args:
            topics: Topics dictionary
            main_topic: List of main topic names to filter
            sub_topic: List of sub topic paths to filter
            topic_value: List of topic values to filter by exact match

        Returns:
            True if matches all filters, False otherwise
        """
        if not topics:
            # If no topics, only match if no filters are specified
            return not main_topic and not sub_topic and not topic_value

        # Check main topic filter
        if main_topic:
            main_topic_match = any(
                self._check_json_path_exists(topics, f"$.{mt}")
                for mt in main_topic
            )
            if not main_topic_match:
                return False

        # Check sub topic filter - only process paths with '.'
        if sub_topic:
            valid_sub_topics = [st for st in sub_topic if '.' in st]
            if valid_sub_topics:  # Only check if there are valid sub_topic paths
                sub_topic_match = any(
                    self._check_json_path_exists(topics, f"$.{st}")
                    for st in valid_sub_topics
                )
                if not sub_topic_match:
                    return False

        # Check topic value filter
        if topic_value:
            valid_values = [tv for tv in topic_value if tv is not None]
            if valid_values:  # Only check if there are valid values
                value_match = any(
                    self._check_topic_value_exists(topics, tv)
                    for tv in valid_values
                )
                if not value_match:
                    return False

        return True

    def _filter_topics_in_memory(
            self,
            topics: Dict[str, Any],
            main_topic: Optional[List[str]],
            sub_topic: Optional[List[str]],
    ) -> Dict[str, Any]:
        """
        Filter topics dictionary in memory after SQL filtering.
        This ensures only matching main topics and sub topics are returned.

        Args:
            topics: Full topics dictionary
            main_topic: Optional list of main topic names to include
            sub_topic: Optional list of sub topic paths to include. Each path should be in the format
                      "main_topic.sub_topic", e.g., ["basic_information.user_name"]

        Returns:
            Filtered topics dictionary
        """
        if not topics or not isinstance(topics, dict):
            return {}

        filtered_result = {}

        for mt, st_dict in topics.items():
            # Check if main topic should be included
            include_main = True
            if main_topic and len(main_topic) > 0:
                include_main = any(mt.lower() == m.lower() for m in main_topic)

            if not include_main:
                continue

            # Filter sub topics
            if isinstance(st_dict, dict):
                filtered_sub = {}
                for st_key, st_value in st_dict.items():
                    # Check if sub topic should be included
                    include_sub = True
                    if sub_topic and len(sub_topic) > 0:
                        # Support both full path format (e.g., "main_topic.sub_topic") and simple sub_topic name
                        include_sub = any(
                            # Match full path: "main_topic.sub_topic"
                            ('.' in s and s.lower() == f"{mt.lower()}.{st_key.lower()}") or
                            # Match simple sub_topic name (backward compatibility)
                            ('.' not in s and st_key.lower() == s.lower())
                            for s in sub_topic
                        )

                    if include_sub:
                        filtered_sub[st_key] = st_value

                # Only add main topic if it has matching sub topics (or no sub_topic filter)
                if filtered_sub or not sub_topic or len(sub_topic) == 0:
                    filtered_result[mt] = filtered_sub
            else:
                # If sub_topic is not a dict, include it if main topic matches
                if include_main:
                    filtered_result[mt] = st_dict

        return filtered_result

    def _build_profile_dict(
            self,
            row: tuple,
            main_topic: Optional[List[str]],
            sub_topic: Optional[List[str]]
    ) -> Dict[str, Any]:
        """
        Build profile dictionary from database row.

        Args:
            row: Database row tuple (id, user_id, profile_content, topics, created_at, updated_at)
            main_topic: Optional list of main topic names for filtering
            sub_topic: Optional list of sub topic paths for filtering

        Returns:
            Profile dictionary
        """
        topics = None
        if row[3]:
            try:
                topics = json.loads(row[3])
            except json.JSONDecodeError:
                topics = None

        # Filter topics in memory after SQL filtering (to return only matching parts)
        if topics and isinstance(topics, dict) and (main_topic or sub_topic):
            topics = self._filter_topics_in_memory(topics, main_topic, sub_topic)

        return {
            "id": row[0],
            "user_id": row[1],
            "profile_content": row[2],
            "topics": topics,
            "created_at": row[4],
            "updated_at": row[5],
        }

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
            user_id: User identifier
            main_topic: Optional list of main topic names to filter
            sub_topic: Optional list of sub topic names to filter by
            topic_value: Optional list of topic values to filter by exact match
            limit: Optional limit on the number of profiles to return (default: 100)
            offset: Optional offset for pagination

        Returns:
            List of profile dictionaries, each with the following keys:
            - "id" (int): Profile ID
            - "user_id" (str): User identifier
            - "profile_content" (str): Profile content text
            - "topics" (dict): Structured topics dictionary (filtered if main_topic or sub_topic provided)
            - "created_at" (str): Creation timestamp in ISO format
            - "updated_at" (str): Last update timestamp in ISO format
            Returns empty list if no profiles found
        """
        with self._lock:
            cursor = self.connection.cursor()

            # Build SQL query - only filter by user_id at SQL level
            # JSON filtering will be done in Python
            sql = f"""
                SELECT {self.primary_field}, user_id, profile_content, topics, created_at, updated_at
                FROM {self.table_name}
            """
            params = []

            if user_id is not None:
                sql += " WHERE user_id = ?"
                params.append(user_id)

            sql += f" ORDER BY {self.primary_field} DESC"

            cursor.execute(sql, params)
            rows = cursor.fetchall()

            # Filter by main_topic, sub_topic, topic_value in Python
            results = []
            for row in rows:
                topics = None
                if row[3]:
                    try:
                        topics = json.loads(row[3])
                    except json.JSONDecodeError:
                        topics = None

                # Check if row matches filters
                if self._matches_filters(topics, main_topic, sub_topic, topic_value):
                    results.append(self._build_profile_dict(row, main_topic, sub_topic))

            # Apply pagination after filtering
            if offset and offset > 0:
                results = results[offset:]
            if limit and limit > 0:
                results = results[:limit]

            return results

    def delete_profile(self, profile_id: int) -> bool:
        """
        Delete user profile by profile_id.

        Args:
            profile_id: Profile ID (Snowflake ID)

        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            cursor = self.connection.cursor()

            cursor.execute(
                f"DELETE FROM {self.table_name} WHERE {self.primary_field} = ?",
                (profile_id,)
            )
            self.connection.commit()

            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug(f"Deleted profile with id: {profile_id}")
            return deleted

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self, 'connection') and self.connection:
            self.connection.close()
            self.connection = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass  # Ignore errors during cleanup
