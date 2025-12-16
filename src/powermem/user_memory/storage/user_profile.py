"""
User Profile storage implementation for OceanBase

This module provides storage for user profile information extracted from conversations.
"""

import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import and_, or_, func, literal, null, Index

from ...storage.oceanbase import constants
from ...utils.utils import serialize_datetime, generate_snowflake_id, get_current_datetime

try:
    from pyobvector import ObVecClient
    from sqlalchemy import Column, String, Table, BigInteger, desc, JSON
    from sqlalchemy.dialects.mysql import LONGTEXT
except ImportError as e:
    raise ImportError(
        f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy."
    )

from .base import UserProfileStoreBase

logger = logging.getLogger(__name__)


class OceanBaseUserProfileStore(UserProfileStoreBase):
    """OceanBase-based user profile storage implementation"""

    def __init__(
            self,
            table_name: str = "user_profiles",
            connection_args: Optional[Dict[str, Any]] = None,
            host: Optional[str] = None,
            port: Optional[str] = None,
            user: Optional[str] = None,
            password: Optional[str] = None,
            db_name: Optional[str] = None,
            **kwargs,
    ):
        """
        Initialize the UserProfileStore.

        Args:
            table_name (str): Name of the table to store user profiles.
            connection_args (Optional[Dict[str, Any]]): Connection parameters for OceanBase.
            host (Optional[str]): OceanBase server host.
            port (Optional[str]): OceanBase server port.
            user (Optional[str]): OceanBase username.
            password (Optional[str]): OceanBase password.
            db_name (Optional[str]): OceanBase database name.
        """
        self.table_name = table_name
        self.primary_field = "id"

        # Handle connection arguments - prioritize individual parameters over connection_args
        if connection_args is None:
            connection_args = {}

        # Merge individual connection parameters with connection_args
        final_connection_args = {
            "host": host or connection_args.get("host", constants.DEFAULT_OCEANBASE_CONNECTION["host"]),
            "port": port or connection_args.get("port", constants.DEFAULT_OCEANBASE_CONNECTION["port"]),
            "user": user or connection_args.get("user", constants.DEFAULT_OCEANBASE_CONNECTION["user"]),
            "password": password or connection_args.get("password", constants.DEFAULT_OCEANBASE_CONNECTION["password"]),
            "db_name": db_name or connection_args.get("db_name", constants.DEFAULT_OCEANBASE_CONNECTION["db_name"]),
        }

        self.connection_args = final_connection_args

        # Initialize client
        self._create_client(**kwargs)
        assert self.obvector is not None

        # Create table if it doesn't exist
        self._create_table()

    def _create_client(self, **kwargs):
        """Create and initialize the OceanBase client."""
        host = self.connection_args.get("host")
        port = self.connection_args.get("port")
        user = self.connection_args.get("user")
        password = self.connection_args.get("password")
        db_name = self.connection_args.get("db_name")

        self.obvector = ObVecClient(
            uri=f"{host}:{port}",
            user=user,
            password=password,
            db_name=db_name,
            **kwargs,
        )

    def _create_table(self) -> None:
        """Create user profiles table if it doesn't exist."""
        if not self.obvector.check_table_exists(self.table_name):
            # Define columns for user profiles table
            cols = [
                # Primary key - Snowflake ID (BIGINT without AUTO_INCREMENT)
                Column(self.primary_field, BigInteger, primary_key=True, autoincrement=False),
                Column("user_id", String(128)),  # User identifier
                Column("profile_content", LONGTEXT),
                Column("topics", JSON),  # Structured topics (main topics and sub-topics)
                Column("created_at", String(128)),
                Column("updated_at", String(128)),
            ]

            # Define regular indexes
            indexes = [
                Index("idx_user_id", "user_id"),
            ]

            # Create table without vector index (simple table)
            self.obvector.create_table_with_index_params(
                table_name=self.table_name,
                columns=cols,
                indexes=indexes,
                vidxs=None,
                partitions=None,
            )

            logger.info(f"Created user profiles table: {self.table_name}")
        else:
            logger.info(f"User profiles table '{self.table_name}' already exists")

        # Load table metadata
        self.table = Table(self.table_name, self.obvector.metadata_obj, autoload_with=self.obvector.engine)

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

        # Check if profile exists with the same combination
        with self.obvector.engine.connect() as conn:
            conditions = [
                self.table.c.user_id == user_id,
            ]

            stmt = self.table.select().where(and_(*conditions)).limit(1)
            result = conn.execute(stmt)
            existing_row = result.fetchone()

            # Prepare update/insert values
            values = {
                "updated_at": now,
            }
            if profile_content is not None:
                values["profile_content"] = profile_content
            if topics is not None:
                values["topics"] = topics

            if existing_row:
                # Update existing record
                profile_id = existing_row.id
                update_stmt = (
                    self.table.update()
                    .where(and_(self.table.c.id == profile_id))
                    .values(**values)
                )
                conn.execute(update_stmt)
                conn.commit()
                logger.debug(f"Updated profile for user_id: {user_id}, profile_id: {profile_id}")
            else:
                # Insert new record
                profile_id = generate_snowflake_id()
                insert_values = {
                    "id": profile_id,
                    "user_id": user_id,
                    "created_at": now,
                    **values,
                }
                insert_stmt = self.table.insert().values(**insert_values)
                conn.execute(insert_stmt)
                conn.commit()
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
        with self.obvector.engine.connect() as conn:
            # Build where condition for user_id only
            condition = self.table.c.user_id == user_id

            # Build select statement
            stmt = self.table.select().where(and_(condition))

            # Order by id desc to get the latest profile
            stmt = stmt.order_by(desc(self.table.c.id))
            stmt = stmt.limit(1)

            result = conn.execute(stmt)
            row = result.fetchone()

            if row:
                return {
                    "id": row.id,
                    "user_id": row.user_id,
                    "profile_content": getattr(row, "profile_content", None),
                    "topics": getattr(row, "topics", None),
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            return None

    def _build_json_path_condition(self, json_path: str) -> Any:
        """
        Build JSON path condition for filtering.

        Args:
            json_path: JSON path in format "$.main_topic" or "$.main_topic.sub_topic"

        Returns:
            SQLAlchemy condition expression
        """
        return func.json_contains_path(
            self.table.c.topics,
            literal('one'),
            literal(json_path)
        ) == 1

    def _build_topic_value_condition(self, value: str) -> Any:
        """
        Build topic value search condition for exact match.

        Args:
            value: Value to search for in topics JSON

        Returns:
            SQLAlchemy condition expression
        """
        return func.json_search(
            self.table.c.topics,
            literal('one'),
            literal(str(value))
        ).isnot(None)

    def _build_filter_conditions(
            self,
            user_id: Optional[str],
            main_topic: Optional[List[str]],
            sub_topic: Optional[List[str]],
            topic_value: Optional[List[str]],
    ) -> List[Any]:
        """
        Build SQL filter conditions based on provided filters.

        Args:
            user_id: User identifier filter
            main_topic: List of main topic names to filter
            sub_topic: List of sub topic paths to filter
            topic_value: List of topic values to filter by exact match

        Returns:
            List of SQLAlchemy condition expressions
        """
        conditions = []

        # User ID filter
        if user_id is not None:
            conditions.append(self.table.c.user_id == user_id)

        # Main topic filter
        if main_topic:
            main_topic_conditions = [
                self._build_json_path_condition(f"$.{mt}")
                for mt in main_topic
            ]
            if main_topic_conditions:
                conditions.append(or_(*main_topic_conditions))

        # Sub topic filter
        if sub_topic:
            sub_topic_conditions = [
                self._build_json_path_condition(f"$.{st}")
                for st in sub_topic
                if '.' in st  # Only process full path format
            ]
            if sub_topic_conditions:
                conditions.append(or_(*sub_topic_conditions))

        # Topic value filter (exact match)
        if topic_value:
            topic_value_conditions = [
                self._build_topic_value_condition(tv)
                for tv in topic_value
                if tv is not None
            ]
            if topic_value_conditions:
                conditions.append(or_(*topic_value_conditions))

        return conditions

    def _build_profile_dict(self, row: Any, main_topic: Optional[List[str]], sub_topic: Optional[List[str]]) -> Dict[str, Any]:
        """
        Build profile dictionary from database row.

        Args:
            row: Database row result
            main_topic: Optional list of main topic names for filtering
            sub_topic: Optional list of sub topic paths for filtering

        Returns:
            Profile dictionary
        """
        topics = getattr(row, "topics", None)

        # Filter topics in memory after SQL filtering (to return only matching parts)
        if topics and isinstance(topics, dict) and (main_topic or sub_topic):
            topics = self._filter_topics_in_memory(topics, main_topic, sub_topic)

        return {
            "id": row.id,
            "user_id": row.user_id,
            "profile_content": getattr(row, "profile_content", None),
            "topics": topics,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
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
        with self.obvector.engine.connect() as conn:
            # Build filter conditions
            conditions = self._build_filter_conditions(
                user_id, main_topic, sub_topic, topic_value
            )

            # Build select statement
            stmt = self.table.select()
            if conditions:
                stmt = stmt.where(and_(*conditions))

            # Order by id desc to get the latest profiles first
            stmt = stmt.order_by(desc(self.table.c.id))

            # Apply pagination
            if offset and offset > 0:
                stmt = stmt.offset(offset)
            if limit and limit > 0:
                stmt = stmt.limit(limit)

            # Execute query and build results
            result = conn.execute(stmt)
            rows = result.fetchall()

            return [
                self._build_profile_dict(row, main_topic, sub_topic)
                for row in rows
            ]

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

    def delete_profile(self, profile_id: int) -> bool:
        """
        Delete user profile by profile_id.

        Args:
            profile_id: Profile ID (Snowflake ID)

        Returns:
            True if deleted, False if not found
        """
        with self.obvector.engine.connect() as conn:
            condition = self.table.c.id == profile_id
            stmt = self.table.delete().where(and_(condition))
            result = conn.execute(stmt)
            conn.commit()

            deleted = result.rowcount > 0
            if deleted:
                logger.debug(f"Deleted profile with id: {profile_id}")
            return deleted
