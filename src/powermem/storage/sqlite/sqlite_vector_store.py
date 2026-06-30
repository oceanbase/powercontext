"""
SQLite vector store implementation

This module provides a simple SQLite-based vector store for development and testing.
Supports FTS5 fulltext search and hybrid search (vector + FTS5, RRF fusion).
"""

import json
import logging
import os
import re
import threading

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase, OutputData
from powermem.utils.utils import generate_snowflake_id

logger = logging.getLogger(__name__)

def _json_path_for_key(key: str) -> str:
    """
    Build a SQLite JSON1 path for `json_extract(payload, ?)` from a dotted key.

    We preserve the previous semantics where dots indicate nesting, but avoid
    SQL injection by *parameterizing the path* instead of interpolating it into SQL.

    Example:
        key="user_id"   -> $."user_id"
        key="a.b"       -> $."a"."b"
        key="x') OR 1=1 -- " -> $."x') OR 1=1 -- "
    """
    segments = key.split(".") if key is not None else [""]
    path = "$"
    for segment in segments:
        path += f".{json.dumps(segment)}"
    return path


# Word fragments aligned with unicode61-style token characters.
_FTS5_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

def _sanitize_fts5_input(query: str) -> str:
    """Build a literal FTS5 MATCH string from user search text.

    Extracts Unicode word tokens and wraps each in double quotes so FTS5 treats
    them as literal terms (including ``AND``/``OR``/``NOT``), not query syntax.
    Punctuation-separated forms such as ``v1.1.6`` become independent token
    matches (``"v1" "1" "6"``) without preserving original spacing. Returns empty
    when nothing remains.
    """
    if not query or not query.strip():
        return ""

    tokens = _FTS5_TOKEN_RE.findall(query)
    if not tokens:
        return ""

    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _check_sqlite_features(conn) -> None:
    """Verify FTS5 and JSON1 are compiled into the SQLite library.

    A version number alone is not sufficient — distributions sometimes ship
    SQLite >= 3.9 without FTS5 or JSON1.  This probe actually exercises both
    features so the error is raised immediately rather than on first use.
    """
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _pm_fts5_probe USING fts5(x)"
        )
        conn.execute("DROP TABLE IF EXISTS _pm_fts5_probe")
    except Exception as exc:
        raise RuntimeError(
            f"SQLite FTS5 extension not available: {exc}. "
            "Install pysqlite3-binary or set DATABASE_PROVIDER=oceanbase."
        ) from exc
    try:
        conn.execute("SELECT json_extract('{}', '$')")
    except Exception as exc:
        raise RuntimeError(
            f"SQLite JSON1 extension not available: {exc}. "
            "Install pysqlite3-binary or set DATABASE_PROVIDER=oceanbase."
        ) from exc


def _extract_fulltext_content(payload: dict) -> str:
    """Extract text content from payload for FTS indexing.

    Primary key is ``fulltext_content`` (set by ``StorageAdapter.add_memory``
    and matching OceanBase's ``fulltext_field``).  Falls back to ``data``
    then ``content`` for payloads inserted without the adapter.
    """
    if not payload:
        return ""
    for key in ("fulltext_content", "data", "content"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val
    return ""


class SQLiteVectorStore(VectorStoreBase):
    """Simple SQLite-based vector store implementation with FTS5 hybrid search."""

    def __init__(self, database_path: str = ":memory:", collection_name: str = "memories",
                 enable_wal: bool = True, timeout: int = 30, **kwargs):
        """
        Initialize SQLite vector store.

        Args:
            database_path: Path to SQLite database file
            collection_name: Name of the collection/table
            enable_wal: Enable WAL journal mode for concurrent read/write
            timeout: Connection timeout in seconds
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
            self.connection = sqlite3.connect(database_path, check_same_thread=False,
                                              timeout=timeout)
            if enable_wal and database_path != ":memory:":
                self.connection.execute("PRAGMA journal_mode=WAL")
            _check_sqlite_features(self.connection)
        except Exception as e:
            logger.error(f"Failed to connect to SQLite database at {database_path}: {e}")
            raise

        # Create the table
        self.create_col()

        logger.info(f"SQLiteVectorStore initialized with db_path: {database_path}")

    def create_col(self, name=None, vector_size=None, distance=None) -> None:
        """Create a new collection (table in SQLite) with FTS5 virtual table."""
        table_name = name or self.collection_name

        with self._lock:
            # Create main table with fulltext_content column
            self.connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY,
                    vector TEXT,  -- Store as JSON string
                    payload TEXT,  -- Store as JSON string
                    fulltext_content TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: add fulltext_content column if table existed without it
            cursor = self.connection.execute(
                f"PRAGMA table_info({table_name})"
            )
            columns = [row[1] for row in cursor.fetchall()]
            if "fulltext_content" not in columns:
                self.connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN fulltext_content TEXT DEFAULT ''"
                )
                logger.info(f"Migrated table {table_name}: added fulltext_content column")

            # Create FTS5 virtual table (content-external mode)
            self.connection.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}_fts USING fts5(
                    fulltext_content,
                    content={table_name},
                    content_rowid=id
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
                ft_content = _extract_fulltext_content(payload)
                self.connection.execute(f"""
                    INSERT INTO {self.collection_name}
                    (id, vector, payload, fulltext_content) VALUES (?, ?, ?, ?)
                """, (vector_id, json.dumps(vector), json.dumps(payload), ft_content))

                # Sync to FTS5 table
                self.connection.execute(f"""
                    INSERT INTO {self.collection_name}_fts(rowid, fulltext_content)
                    VALUES (?, ?)
                """, (vector_id, ft_content))

            self.connection.commit()

        return generated_ids

    def search(self, query: str, vectors: List[List[float]] = None, limit: int = 5, filters=None) -> List[OutputData]:
        """Search using vector similarity, fulltext, or hybrid (RRF fusion).

        - Both ``query`` and ``vectors`` provided: hybrid search (vector + FTS5, RRF fusion)
        - Only ``vectors``: pure vector cosine similarity
        - Only ``query`` (non-empty string): pure FTS5 fulltext search
        - Neither: returns empty list
        """
        has_vectors = vectors is not None and len(vectors) > 0
        has_query = isinstance(query, str) and query.strip() != ""

        if has_vectors and has_query:
            # Hybrid search: vector + FTS5, combined with RRF
            vector_results = self._vector_search(vectors[0], limit, filters)
            fts_results = self._fulltext_search(query, limit, filters)
            return self._rrf_fusion(vector_results, fts_results, limit)
        elif has_vectors:
            # Pure vector search (backward compatible)
            return self._vector_search(vectors[0], limit, filters)
        elif has_query:
            # Pure fulltext search
            return self._fulltext_search(query, limit, filters)
        else:
            # Fallback: if query is a list (legacy), use as vector
            if isinstance(query, list):
                return self._vector_search(query, limit, filters)
            return []

    def _vector_search(self, query_vector: List[float], limit: int = 5,
                       filters=None) -> List[OutputData]:
        """Pure vector cosine similarity search."""
        results = []

        # Build query with filters
        query_sql = f"SELECT id, vector, payload FROM {self.collection_name}"
        query_params = []

        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append("(json_extract(payload, ?) = ?)")
                query_params.extend([_json_path_for_key(key), value])

            if conditions:
                query_sql += " WHERE " + " AND ".join(conditions)
                logger.info(f"SQLite vector search with filters: {query_sql}")

        with self._lock:
            if query_params:
                cursor = self.connection.execute(query_sql, query_params)
            else:
                cursor = self.connection.execute(query_sql)

            for row in cursor.fetchall():
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)

                similarity = self._cosine_similarity(query_vector, vector)
                payload['_vector_similarity'] = similarity

                results.append(OutputData(
                    id=vector_id,
                    score=similarity,
                    payload=payload
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def _fulltext_search(self, query: str, limit: int = 5,
                         filters=None) -> List[OutputData]:
        """Perform FTS5 fulltext search with bm25 scoring.

        Uses the ``{collection}_fts`` virtual table created by ``create_col()``.
        Joins back to the main table for payload and applies JSON-based filters.
        """
        if not query or not query.strip():
            return []

        fts_query = _sanitize_fts5_input(query)
        if not fts_query:
            logger.debug(
                "FTS5 query sanitized to empty, skipping fulltext search"
            )
            return []

        fts_table = f"{self.collection_name}_fts"
        main_table = self.collection_name

        # Build the query joining FTS results back to main table
        sql = (
            f"SELECT m.id, m.payload, -rank AS score "
            f"FROM {fts_table} f "
            f"JOIN {main_table} m ON m.id = f.rowid "
            f"WHERE {fts_table} MATCH ? "
        )

        filter_params: List = []
        if filters:
            for key, value in filters.items():
                sql += " AND (json_extract(m.payload, ?) = ?) "
                filter_params.extend([_json_path_for_key(key), value])

        sql += " ORDER BY rank LIMIT ?"

        params: List = [fts_query, *filter_params, limit]
        results: List[OutputData] = []
        with self._lock:
            try:
                cursor = self.connection.execute(sql, params)
                for row in cursor.fetchall():
                    doc_id, payload_str, fts_score = row
                    payload = json.loads(payload_str)
                    payload['_fts_score'] = float(fts_score)

                    results.append(OutputData(
                        id=doc_id,
                        score=float(fts_score),
                        payload=payload,
                    ))
            except sqlite3.OperationalError as exc:
                logger.warning(f"FTS5 search failed, returning empty: {exc}")
                results = []

        return results

    def _rrf_fusion(self, vector_results: List[OutputData],
                    fts_results: List[OutputData], limit: int,
                    k: int = 60,
                    vector_weight: float = 0.5,
                    fts_weight: float = 0.5) -> List[OutputData]:
        """Reciprocal Rank Fusion combining vector and FTS5 results.

        Simplified 2-way RRF (no sparse path) modeled after
        ``OceanBaseVectorStore._rrf_fusion``.
        """
        all_docs: Dict[int, dict] = {}

        # Process vector results
        for rank, result in enumerate(vector_results, 1):
            rrf_score = vector_weight * (1.0 / (k + rank))
            all_docs[result.id] = {
                'result': result,
                'vector_rank': rank,
                'fts_rank': None,
                'rrf_score': rrf_score,
            }

        # Process FTS results
        for rank, result in enumerate(fts_results, 1):
            fts_rrf = fts_weight * (1.0 / (k + rank))

            if result.id in all_docs:
                all_docs[result.id]['fts_rank'] = rank
                all_docs[result.id]['rrf_score'] += fts_rrf
                # Merge FTS score into payload
                all_docs[result.id]['result'].payload['_fts_score'] = result.payload.get('_fts_score')
            else:
                all_docs[result.id] = {
                    'result': result,
                    'vector_rank': None,
                    'fts_rank': rank,
                    'rrf_score': fts_rrf,
                }

        # Sort by RRF score descending, take top ``limit``
        sorted_docs = sorted(all_docs.values(), key=lambda d: d['rrf_score'], reverse=True)

        final_results = []
        for doc_data in sorted_docs[:limit]:
            result = doc_data['result']
            score = doc_data['rrf_score']

            result.score = score
            result.payload['_fusion_score'] = score
            result.payload['_fusion_info'] = {
                'vector_rank': doc_data['vector_rank'],
                'fts_rank': doc_data['fts_rank'],
                'rrf_score': score,
                'fusion_method': 'rrf',
                'vector_weight': vector_weight,
                'fts_weight': fts_weight,
            }
            final_results.append(result)

        return final_results

    def delete(self, vector_id: int) -> None:
        """Delete a vector by ID."""
        with self._lock:
            # Delete from FTS5 first (content-external requires explicit delete)
            try:
                # Read current fulltext content for FTS delete
                cursor = self.connection.execute(
                    f"SELECT fulltext_content FROM {self.collection_name} WHERE id = ?",
                    (vector_id,),
                )
                row = cursor.fetchone()
                if row:
                    self.connection.execute(
                        f"INSERT INTO {self.collection_name}_fts({self.collection_name}_fts, rowid, fulltext_content) "
                        f"VALUES ('delete', ?, ?)",
                        (vector_id, row[0] or ""),
                    )

                self.connection.execute(f"""
                    DELETE FROM {self.collection_name} WHERE id = ?
                """, (vector_id,))
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

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
            # Also update fulltext_content column
            ft_content = _extract_fulltext_content(payload)
            updates.append("fulltext_content = ?")
            values.append(ft_content)

        if updates:
            values.append(vector_id)
            with self._lock:
                # Read old fulltext content BEFORE updating (needed for FTS delete)
                old_content = ""
                if payload is not None:
                    cursor = self.connection.execute(
                        f"SELECT fulltext_content FROM {self.collection_name} WHERE id = ?",
                        (vector_id,),
                    )
                    old_row = cursor.fetchone()
                    old_content = (old_row[0] or "") if old_row else ""

                try:
                    # Update main table
                    self.connection.execute(f"""
                        UPDATE {self.collection_name}
                        SET {', '.join(updates)}
                        WHERE id = ?
                    """, values)

                    # FTS sync within the same implicit transaction
                    if payload is not None:
                        fts_table = f"{self.collection_name}_fts"
                        self.connection.execute(
                            f"INSERT INTO {fts_table}({fts_table}, rowid, fulltext_content) "
                            f"VALUES ('delete', ?, ?)",
                            (vector_id, old_content),
                        )
                        new_content = _extract_fulltext_content(payload)
                        self.connection.execute(
                            f"INSERT INTO {fts_table}(rowid, fulltext_content) "
                            f"VALUES (?, ?)",
                            (vector_id, new_content),
                        )

                    self.connection.commit()
                except Exception:
                    self.connection.rollback()
                    raise

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
        """List all collections (tables), excluding FTS5 virtual and shadow tables."""
        with self._lock:
            # Identify FTS5 virtual tables by their CREATE VIRTUAL TABLE statement
            cursor = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE sql LIKE '%USING fts5%'"
            )
            fts_virtuals = {row[0] for row in cursor.fetchall()}
            fts_prefixes = tuple(name + "_" for name in fts_virtuals)

            # Return only tables that are neither FTS5 virtual tables nor shadow tables
            cursor = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            return [
                row[0] for row in cursor.fetchall()
                if row[0] not in fts_virtuals
                and not (fts_prefixes and row[0].startswith(fts_prefixes))
            ]

    def delete_col(self) -> None:
        """Delete the collection (table) and its FTS5 virtual table."""
        with self._lock:
            self.connection.execute(f"DROP TABLE IF EXISTS {self.collection_name}_fts")
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
                conditions.append("(json_extract(payload, ?) = ?)")
                query_params.extend([_json_path_for_key(key), value])

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
        if limit is not None:
            query += f" LIMIT {limit}"
        elif offset is not None:
            query += " LIMIT -1"
        if offset is not None:
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

    def count(self, filters=None) -> int:
        """Count all memories with optional filtering.

        Args:
            filters: Optional filters dictionary

        Returns:
            int: Total count of memories matching the filters
        """
        query = f"SELECT COUNT(*) FROM {self.collection_name}"
        query_params = []

        # Apply filters if provided
        if filters:
            conditions = []
            for key, value in filters.items():
                # Filter by JSON field in payload
                conditions.append("(json_extract(payload, ?) = ?)")
                query_params.extend([_json_path_for_key(key), value])

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        with self._lock:
            if query_params:
                cursor = self.connection.execute(query, query_params)
            else:
                cursor = self.connection.execute(query)

            count = cursor.fetchone()[0]

        return count

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
                conditions.append("(json_extract(payload, ?) = ?)")
                query_params.extend([_json_path_for_key(key), value])
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
