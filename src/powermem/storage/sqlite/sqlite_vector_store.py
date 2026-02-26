"""
SQLite vector store implementation

This module provides a simple SQLite-based vector store for development and testing.
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase, OutputData
from powermem.utils.utils import generate_snowflake_id

logger = logging.getLogger(__name__)


class SQLiteVectorStore(VectorStoreBase):
    """Simple SQLite-based vector store implementation."""
    
    def __init__(self, database_path: str = ":memory:", collection_name: str = "memories", **kwargs):
        """
        Initialize SQLite vector store.
        
        Args:
            database_path: Path to SQLite database file
            collection_name: Name of the collection/table
        """
        self.db_path = database_path
        self.collection_name = collection_name
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
        
        # Create the table
        self.create_col()
        
        logger.info(f"SQLiteVectorStore initialized with db_path: {database_path}")
    
    def create_col(self, name=None, vector_size=None, distance=None) -> None:
        """Create a new collection (table in SQLite)."""
        table_name = name or self.collection_name
        
        with self._lock:
            self.connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY,
                    vector TEXT,  -- Store as JSON string
                    payload TEXT,  -- Store as JSON string
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.connection.commit()
    
    def insert(self, vectors: List[List[float]], payloads=None, ids=None) -> List[int]:
        """
        Insert vectors into the collection.
        
        Args:
            vectors: List of vectors to insert
            payloads: List of payload dictionaries
            ids: Deprecated parameter (ignored), IDs are now generated using Snowflake algorithm
            
        Returns:
            List[int]: List of generated Snowflake IDs
        """
        if not vectors:
            return []
        
        if payloads is None:
            payloads = [{} for _ in vectors]
        
        # Generate Snowflake IDs for each vector
        generated_ids = [generate_snowflake_id() for _ in range(len(vectors))]
        
        with self._lock:
            for vector, payload, vector_id in zip(vectors, payloads, generated_ids):
                self.connection.execute(f"""
                    INSERT INTO {self.collection_name} 
                    (id, vector, payload) VALUES (?, ?, ?)
                """, (vector_id, json.dumps(vector), json.dumps(payload)))
            
            self.connection.commit()
        
        return generated_ids
    
    def search(self, query: str, vectors: List[List[float]] = None, limit: int = 5, filters=None) -> List[OutputData]:
        """Search for similar vectors using simple cosine similarity."""
        results = []
        
        # Extract query vector from vectors parameter (OceanBase format)
        if vectors and len(vectors) > 0:
            query_vector = vectors[0]
        else:
            # Fallback for backward compatibility
            query_vector = query if isinstance(query, list) else [0.1] * 10
        
        # Build query with filters
        query_sql = f"SELECT id, vector, payload FROM {self.collection_name}"
        query_params = []
        
        # Apply filters if provided
        if filters:
            conditions = []
            for key, value in filters.items():
                # Filter by JSON field in payload
                conditions.append(f"json_extract(payload, '$.{key}') = ?")
                query_params.append(value)
            
            if conditions:
                query_sql += " WHERE " + " AND ".join(conditions)
                logger.info(f"SQLite search with filters: {query_sql}, params: {query_params}")
            else:
                logger.debug("SQLite search: filters provided but empty after processing")
        else:
            logger.debug("SQLite search: no filters provided")
        
        with self._lock:
            if query_params:
                cursor = self.connection.execute(query_sql, query_params)
            else:
                cursor = self.connection.execute(query_sql)
            
            row_count = 0
            for row in cursor.fetchall():
                row_count += 1
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                # Calculate cosine similarity
                similarity = self._cosine_similarity(query_vector, vector)
                
                results.append(OutputData(
                    id=vector_id,
                    score=similarity,
                    payload=payload
                ))
        
        # Sort by similarity (descending) and return top results
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]
    
    def delete(self, vector_id: int) -> None:
        """Delete a vector by ID."""
        with self._lock:
            self.connection.execute(f"""
                DELETE FROM {self.collection_name} WHERE id = ?
            """, (vector_id,))
            self.connection.commit()
    
    def update(self, vector_id: int, vector=None, payload=None) -> None:
        """Update a vector and its payload."""
        updates = []
        values = []
        
        if vector is not None:
            updates.append("vector = ?")
            values.append(json.dumps(vector))
        
        if payload is not None:
            updates.append("payload = ?")
            values.append(json.dumps(payload))
        
        if updates:
            values.append(vector_id)
            with self._lock:
                self.connection.execute(f"""
                    UPDATE {self.collection_name} 
                    SET {', '.join(updates)}
                    WHERE id = ?
                """, values)
                self.connection.commit()
    
    def get(self, vector_id: int) -> Optional[OutputData]:
        """Retrieve a vector by ID."""
        with self._lock:
            cursor = self.connection.execute(f"""
                SELECT id, vector, payload FROM {self.collection_name} WHERE id = ?
            """, (vector_id,))
            
            row = cursor.fetchone()
            if row:
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                return OutputData(
                    id=vector_id,
                    score=1.0,  # Exact match
                    payload=payload
                )
        
        return None
    
    def list_cols(self) -> List[str]:
        """List all collections (tables)."""
        with self._lock:
            cursor = self.connection.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
            """)
            return [row[0] for row in cursor.fetchall()]
    
    def delete_col(self) -> None:
        """Delete the collection (table)."""
        with self._lock:
            self.connection.execute(f"DROP TABLE IF EXISTS {self.collection_name}")
            self.connection.commit()
    
    def col_info(self) -> Dict[str, Any]:
        """Get information about the collection."""
        with self._lock:
            cursor = self.connection.execute(f"""
                SELECT COUNT(*) FROM {self.collection_name}
            """)
            count = cursor.fetchone()[0]
            
            return {
                "name": self.collection_name,
                "count": count,
                "db_path": self.db_path
            }
    
    def list(self, filters=None, limit=None, offset=None, order_by=None, order="desc") -> List[OutputData]:
        """List all memories with optional filtering, pagination and sorting."""
        query = f"SELECT id, vector, payload FROM {self.collection_name}"
        query_params = []
        
        # Apply filters if provided
        if filters:
            conditions = []
            for key, value in filters.items():
                # Filter by JSON field in payload
                conditions.append(f"json_extract(payload, '$.{key}') = ?")
                query_params.append(value)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
        
        # Add ORDER BY clause for sorting
        if order_by:
            order_upper = order.upper()
            if order_by in ["created_at", "updated_at"]:
                # Sort by JSON field in payload
                query += f" ORDER BY json_extract(payload, '$.{order_by}') {order_upper}"
            elif order_by == "id":
                # Sort by id column
                query += f" ORDER BY id {order_upper}"
        
        # Add LIMIT and OFFSET for pagination
        # Note: In SQLite, LIMIT must come after ORDER BY and before OFFSET
        if limit:
            query += f" LIMIT {limit}"
        if offset:
            query += f" OFFSET {offset}"
        
        results = []
        with self._lock:
            if query_params:
                cursor = self.connection.execute(query, query_params)
            else:
                cursor = self.connection.execute(query)
            
            for row in cursor.fetchall():
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                results.append(OutputData(
                    id=vector_id,
                    score=1.0,
                    payload=payload
                ))
        
        return results
    
    def reset(self) -> None:
        """Reset by deleting and recreating the collection."""
        self.delete_col()
        self.create_col()

    def get_statistics(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get statistics for the memories in SQLite."""
        query = f"SELECT id, payload, created_at FROM {self.collection_name}"
        query_params = []

        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(f"json_extract(payload, '$.{key}') = ?")
                query_params.append(value)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        stats = {
            "total_memories": 0,
            "by_type": {},
            "avg_importance": 0.0,
            "top_accessed": [],
            "growth_trend": {},
            "age_distribution": {
                "< 1 day": 0,
                "1-7 days": 0,
                "7-30 days": 0,
                "> 30 days": 0,
            },
        }

        total_importance = 0.0
        importance_count = 0

        with self._lock:
            cursor = self.connection.execute(query, query_params)
            rows = cursor.fetchall()

            stats["total_memories"] = len(rows)
            if not rows:
                return stats

            from datetime import datetime

            now = datetime.now()

            memories_with_access = []

            for row in rows:
                row_id, payload_str, created_at_str = row
                payload = json.loads(payload_str)

                # Type distribution (category is the unified field for memory type)
                m_type = payload.get("category") or payload.get("type") or "unknown"
                stats["by_type"][m_type] = stats["by_type"].get(m_type, 0) + 1

                # Importance (usually nested in metadata)
                user_metadata = payload.get("metadata", {})
                importance = user_metadata.get("importance") or payload.get(
                    "importance"
                )
                if importance is not None:
                    try:
                        total_importance += float(importance)
                        importance_count += 1
                    except (ValueError, TypeError):
                        pass

                # Access count for top_accessed (usually nested in metadata)
                access_count = (
                    user_metadata.get("access_count")
                    or payload.get("access_count")
                    or 0
                )

                # Content (unified field name is 'data')
                content = payload.get("data") or payload.get("content") or ""

                memories_with_access.append(
                    {
                        "id": row_id,
                        "content": content[:50],
                        "access_count": int(access_count),
                    }
                )

                # Growth trend (by date)
                if created_at_str:
                    date_part = created_at_str.split(" ")[0]
                    stats["growth_trend"][date_part] = (
                        stats["growth_trend"].get(date_part, 0) + 1
                    )

                    # Age distribution
                    try:
                        # SQLite created_at is usually 'YYYY-MM-DD HH:MM:SS'
                        created_at = datetime.fromisoformat(
                            created_at_str.replace(" ", "T")
                        )
                        days_old = (now - created_at).days
                        if days_old < 1:
                            stats["age_distribution"]["< 1 day"] += 1
                        elif days_old < 7:
                            stats["age_distribution"]["1-7 days"] += 1
                        elif days_old < 30:
                            stats["age_distribution"]["7-30 days"] += 1
                        else:
                            stats["age_distribution"]["> 30 days"] += 1
                    except Exception:
                        pass

            if importance_count > 0:
                stats["avg_importance"] = round(total_importance / importance_count, 2)

            # Sort top accessed and take top 10
            memories_with_access.sort(key=lambda x: x["access_count"], reverse=True)
            stats["top_accessed"] = memories_with_access[:10]

        return stats

    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs from SQLite."""
        query = f"SELECT DISTINCT json_extract(payload, '$.user_id') FROM {self.collection_name}"

        users = []
        with self._lock:
            cursor = self.connection.execute(query)
            for row in cursor.fetchall():
                if row[0]:
                    users.append(str(row[0]))

        return users

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)
    
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
