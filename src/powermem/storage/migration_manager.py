"""
Sub-store migration status management module.

This module provides a database-backed migration status manager for sub-stores.
"""

import logging
import uuid
import json
from datetime import datetime
from powermem.utils.utils import get_current_datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class MigrationStatus:
    """Migration status enum"""
    PENDING = "pending"        # Registered, not started
    MIGRATING = "migrating"    # Migration in progress
    COMPLETED = "completed"    # Completed, ready for routing
    FAILED = "failed"          # Migration failed


class SubStoreMigrationManager:
    """
    Sub-store migration status manager (database-backed).
    
    This class manages migration status for sub-stores using a database table.
    All status information is persisted in the database, supporting multi-process
    sharing and application restarts.
    """
    
    def __init__(self, vector_store, main_collection_name: str):
        """
        Initialize migration manager.
        
        Args:
            vector_store: Vector store instance (must support SQL operations)
            main_collection_name: Main collection name
        """
        self.vector_store = vector_store
        self.main_collection_name = main_collection_name
        
        # Table name for migration status
        self.status_table = "sub_store_migration_status"
        
        # Ensure table exists
        self._init_status_table()
    
    def _init_status_table(self):
        """Initialize migration status table"""
        try:
            # Check if vector store supports SQL operations
            if not hasattr(self.vector_store, 'execute_sql'):
                logger.warning("Vector store does not support SQL operations, migration status will not be persisted")
                return
            
            # Create table if not exists
            create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {self.status_table} (
                id VARCHAR(64) PRIMARY KEY,
                main_collection_name VARCHAR(128) NOT NULL,
                sub_store_name VARCHAR(128) NOT NULL,
                routing_filter TEXT,
                status VARCHAR(32) DEFAULT 'pending',
                migrated_count INT DEFAULT 0,
                total_count INT DEFAULT 0,
                error_message TEXT,
                created_at VARCHAR(128),
                updated_at VARCHAR(128),
                started_at VARCHAR(128),
                completed_at VARCHAR(128),
                UNIQUE KEY unique_sub_store (main_collection_name, sub_store_name),
                KEY idx_main_collection (main_collection_name),
                KEY idx_status (status)
            )
            """
            
            self.vector_store.execute_sql(create_table_sql)
            logger.info(f"Migration status table '{self.status_table}' initialized")
            
        except Exception as e:
            logger.warning(f"Failed to initialize migration status table: {e}")
            logger.warning("Migration status will not be persisted")
    
    def register_sub_store(
        self,
        sub_store_name: str,
        routing_filter: Dict
    ):
        """
        Register a sub store with pending status.
        
        Args:
            sub_store_name: Sub store name
            routing_filter: Routing filter dict
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                logger.debug("Skipping registration: vector store does not support SQL")
                return
            
            status_id = str(uuid.uuid4())
            now = get_current_datetime().isoformat()
            
            # Convert routing filter to JSON string
            routing_filter_json = json.dumps(routing_filter)
            
            # Insert with ON DUPLICATE KEY UPDATE (for MySQL/OceanBase)
            insert_sql = f"""
            INSERT INTO {self.status_table} 
                (id, main_collection_name, sub_store_name, routing_filter, status, created_at, updated_at)
            VALUES 
                ('{status_id}', '{self.main_collection_name}', '{sub_store_name}', 
                 '{routing_filter_json}', '{MigrationStatus.PENDING}', '{now}', '{now}')
            ON DUPLICATE KEY UPDATE
                routing_filter = '{routing_filter_json}',
                updated_at = '{now}'
            """
            
            self.vector_store.execute_sql(insert_sql)
            logger.info(f"Registered sub store '{sub_store_name}' with status: pending")
            
        except Exception as e:
            logger.error(f"Failed to register sub store: {e}")
    
    def is_ready(self, sub_store_name: str) -> bool:
        """
        Check if sub store is ready (migration completed).
        
        Args:
            sub_store_name: Sub store name
            
        Returns:
            True if migration completed, False otherwise
        """
        try:
            status = self.get_status(sub_store_name)
            if status:
                return status.get("status") == MigrationStatus.COMPLETED
            return False
            
        except Exception as e:
            logger.error(f"Failed to check sub store readiness: {e}")
            return False
    
    def mark_migrating(self, sub_store_name: str, total_count: int):
        """
        Mark sub store as migrating.
        
        Args:
            sub_store_name: Sub store name
            total_count: Total number of records to migrate
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return
            
            now = get_current_datetime().isoformat()
            
            update_sql = f"""
            UPDATE {self.status_table}
            SET status = '{MigrationStatus.MIGRATING}',
                total_count = {total_count},
                started_at = '{now}',
                updated_at = '{now}'
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            """
            
            self.vector_store.execute_sql(update_sql)
            logger.info(f"Marked sub store '{sub_store_name}' as migrating (total: {total_count})")
            
        except Exception as e:
            logger.error(f"Failed to mark sub store as migrating: {e}")
    
    def update_progress(self, sub_store_name: str, migrated_count: int):
        """
        Update migration progress.
        
        Args:
            sub_store_name: Sub store name
            migrated_count: Number of migrated records
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return
            
            now = get_current_datetime().isoformat()
            
            update_sql = f"""
            UPDATE {self.status_table}
            SET migrated_count = {migrated_count},
                updated_at = '{now}'
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            """
            
            self.vector_store.execute_sql(update_sql)
            logger.debug(f"Updated progress for '{sub_store_name}': {migrated_count}")
            
        except Exception as e:
            logger.error(f"Failed to update progress: {e}")
    
    def mark_completed(self, sub_store_name: str, migrated_count: int):
        """
        Mark sub store migration as completed.
        
        Args:
            sub_store_name: Sub store name
            migrated_count: Final migrated count
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return
            
            now = get_current_datetime().isoformat()
            
            update_sql = f"""
            UPDATE {self.status_table}
            SET status = '{MigrationStatus.COMPLETED}',
                migrated_count = {migrated_count},
                completed_at = '{now}',
                updated_at = '{now}',
                error_message = NULL
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            """
            
            self.vector_store.execute_sql(update_sql)
            logger.info(f"Marked sub store '{sub_store_name}' as completed ({migrated_count} records)")
            
        except Exception as e:
            logger.error(f"Failed to mark sub store as completed: {e}")
    
    def mark_failed(self, sub_store_name: str, error_message: str):
        """
        Mark sub store migration as failed.
        
        Args:
            sub_store_name: Sub store name
            error_message: Error message
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return
            
            now = get_current_datetime().isoformat()
            
            # Escape single quotes in error message
            error_message_escaped = error_message.replace("'", "''")
            
            update_sql = f"""
            UPDATE {self.status_table}
            SET status = '{MigrationStatus.FAILED}',
                error_message = '{error_message_escaped}',
                updated_at = '{now}'
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            """
            
            self.vector_store.execute_sql(update_sql)
            logger.error(f"Marked sub store '{sub_store_name}' as failed: {error_message}")
            
        except Exception as e:
            logger.error(f"Failed to mark sub store as failed: {e}")
    
    def get_status(self, sub_store_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed status for a sub store.
        
        Args:
            sub_store_name: Sub store name
            
        Returns:
            Status dict or None if not found
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return None
            
            query_sql = f"""
            SELECT * FROM {self.status_table}
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            LIMIT 1
            """
            
            result = self.vector_store.execute_sql(query_sql)
            
            if result and len(result) > 0:
                row = result[0]
                return {
                    "id": row.get("id"),
                    "main_collection_name": row.get("main_collection_name"),
                    "sub_store_name": row.get("sub_store_name"),
                    "routing_filter": json.loads(row.get("routing_filter", "{}")),
                    "status": row.get("status"),
                    "migrated_count": row.get("migrated_count", 0),
                    "total_count": row.get("total_count", 0),
                    "error_message": row.get("error_message"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "started_at": row.get("started_at"),
                    "completed_at": row.get("completed_at"),
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return None
    
    def get_migration_progress(self, sub_store_name: str) -> Optional[Dict[str, Any]]:
        """
        Get migration progress including percentage.
        
        Args:
            sub_store_name: Sub store name
            
        Returns:
            Progress dict or None if not found
        """
        try:
            status = self.get_status(sub_store_name)
            if not status:
                return None
            
            total_count = status.get("total_count", 0)
            migrated_count = status.get("migrated_count", 0)
            
            # Calculate percentage
            if total_count > 0:
                progress_percentage = (migrated_count / total_count) * 100
            else:
                progress_percentage = 0.0
            
            # Calculate elapsed time
            started_at = status.get("started_at")
            updated_at = status.get("updated_at")
            elapsed_seconds = None
            
            if started_at and updated_at:
                try:
                    start_time = datetime.fromisoformat(started_at)
                    current_time = datetime.fromisoformat(updated_at)
                    elapsed_seconds = (current_time - start_time).total_seconds()
                except Exception:
                    pass
            
            return {
                "sub_store_name": sub_store_name,
                "status": status.get("status"),
                "progress_percentage": progress_percentage,
                "migrated_count": migrated_count,
                "total_count": total_count,
                "elapsed_seconds": elapsed_seconds,
                "error_message": status.get("error_message"),
                "is_ready": status.get("status") == MigrationStatus.COMPLETED
            }
            
        except Exception as e:
            logger.error(f"Failed to get migration progress: {e}")
            return None
    
    def list_all_status(self) -> List[Dict[str, Any]]:
        """
        List status for all sub stores under this main collection.
        
        Returns:
            List of status dicts
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return []
            
            query_sql = f"""
            SELECT * FROM {self.status_table}
            WHERE main_collection_name = '{self.main_collection_name}'
            ORDER BY created_at
            """
            
            results = self.vector_store.execute_sql(query_sql)
            
            status_list = []
            for row in results:
                status_list.append({
                    "id": row.get("id"),
                    "sub_store_name": row.get("sub_store_name"),
                    "routing_filter": json.loads(row.get("routing_filter", "{}")),
                    "status": row.get("status"),
                    "migrated_count": row.get("migrated_count", 0),
                    "total_count": row.get("total_count", 0),
                    "error_message": row.get("error_message"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "started_at": row.get("started_at"),
                    "completed_at": row.get("completed_at"),
                })
            
            return status_list
            
        except Exception as e:
            logger.error(f"Failed to list all status: {e}")
            return []
    
    def reset_status(self, sub_store_name: str):
        """
        Reset sub store migration status to pending (for retry).
        
        Args:
            sub_store_name: Sub store name
        """
        try:
            if not hasattr(self.vector_store, 'execute_sql'):
                return
            
            now = get_current_datetime().isoformat()
            
            update_sql = f"""
            UPDATE {self.status_table}
            SET status = '{MigrationStatus.PENDING}',
                migrated_count = 0,
                total_count = 0,
                error_message = NULL,
                started_at = NULL,
                completed_at = NULL,
                updated_at = '{now}'
            WHERE main_collection_name = '{self.main_collection_name}'
              AND sub_store_name = '{sub_store_name}'
            """
            
            self.vector_store.execute_sql(update_sql)
            logger.info(f"Reset migration status for sub store '{sub_store_name}'")
            
        except Exception as e:
            logger.error(f"Failed to reset status: {e}")

