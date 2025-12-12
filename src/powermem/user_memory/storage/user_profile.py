"""
User Profile storage implementation for OceanBase

This module provides storage for user profile information extracted from conversations.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import and_, JSON

from ...storage.oceanbase import constants
from ...utils.utils import serialize_datetime, generate_snowflake_id, get_current_datetime

try:
    from pyobvector import ObVecClient
    from sqlalchemy import Column, String, Table, BigInteger, desc
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
                Column("agent_id", String(128)),  # Agent identifier
                Column("run_id", String(128)),  # Run identifier
                Column("profile_content", LONGTEXT),
                Column("topics", JSON),  # Structured topics (main topics and sub-topics)
                Column("created_at", String(128)),
                Column("updated_at", String(128)),
            ]

            # Create table without vector index (simple table)
            self.obvector.create_table_with_index_params(
                table_name=self.table_name,
                columns=cols,
                indexes=None,
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
        now = serialize_datetime(get_current_datetime())
        
        # Normalize empty strings to None for comparison
        agent_id_normalized = agent_id or ""
        run_id_normalized = run_id or ""
        
        # Check if profile exists with the same combination
        with self.obvector.engine.connect() as conn:
            conditions = [
                self.table.c.user_id == user_id,
                self.table.c.agent_id == agent_id_normalized,
                self.table.c.run_id == run_id_normalized,
            ]
            
            stmt = self.table.select().where(and_(*conditions)).limit(1)
            result = conn.execute(stmt)
            existing_row = result.fetchone()
            
            if existing_row:
                # Update existing record
                profile_id = existing_row.id
                update_stmt = (
                    self.table.update()
                    .where(and_(self.table.c.id == profile_id))
                    .values(
                        profile_content=profile_content,
                        updated_at=now,
                    )
                )
                conn.execute(update_stmt)
                conn.commit()
                logger.debug(f"Updated profile for user_id: {user_id}, profile_id: {profile_id}")
            else:
                # Insert new record
                profile_id = generate_snowflake_id()
                insert_stmt = self.table.insert().values(
                    id=profile_id,
                    user_id=user_id,
                    agent_id=agent_id_normalized,
                    run_id=run_id_normalized,
                    profile_content=profile_content,
                    created_at=now,
                    updated_at=now,
                )
                conn.execute(insert_stmt)
                conn.commit()
                logger.debug(f"Created profile for user_id: {user_id}, profile_id: {profile_id}")
        
        return profile_id

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
            Profile dictionary with the following keys:
            - "id" (int): Profile ID
            - "user_id" (str): User identifier
            - "agent_id" (str): Agent identifier
            - "run_id" (str): Run identifier
            - "profile_content" (str): Profile content text
            - "created_at" (str): Creation timestamp in ISO format
            - "updated_at" (str): Last update timestamp in ISO format
            or None if not found
        """
        with self.obvector.engine.connect() as conn:
            # Build where conditions
            conditions = [self.table.c.user_id == user_id,
                          self.table.c.agent_id == (agent_id or ""),
                          self.table.c.run_id == (run_id or "")]
            
            stmt = self.table.select().where(and_(*conditions))
            
            # Order by id desc to get the latest profile
            stmt = stmt.order_by(desc(self.table.c.id))
            stmt = stmt.limit(1)
            
            result = conn.execute(stmt)
            row = result.fetchone()

            if row:
                return {
                    "id": row.id,
                    "user_id": row.user_id,
                    "agent_id": row.agent_id,
                    "run_id": row.run_id,
                    "profile_content": row.profile_content,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            return None

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