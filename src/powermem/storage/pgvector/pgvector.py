import json
import logging
from contextlib import contextmanager
from typing import Any, List, Optional, Dict

from powermem.storage.base import VectorStoreBase, OutputData
from powermem.utils.utils import generate_snowflake_id

# Try to import psycopg (psycopg3) first, then fall back to psycopg2
try:
    from psycopg.types.json import Json
    from psycopg_pool import ConnectionPool
    PSYCOPG_VERSION = 3
    logger = logging.getLogger(__name__)
    logger.info("Using psycopg (psycopg3) with ConnectionPool for PostgreSQL connections")
except ImportError:
    try:
        from psycopg2.extras import Json, execute_values
        from psycopg2.pool import ThreadedConnectionPool as ConnectionPool
        PSYCOPG_VERSION = 2
        logger = logging.getLogger(__name__)
        logger.info("Using psycopg2 with ThreadedConnectionPool for PostgreSQL connections")
    except ImportError:
        raise ImportError(
            "Neither 'psycopg' nor 'psycopg2' library is available. "
            "Please install one of them using 'pip install psycopg[pool]' or 'pip install psycopg2'"
        )

logger = logging.getLogger(__name__)

class PGVectorStore(VectorStoreBase):
    def __init__(
        self,
        dbname,
        collection_name,
        embedding_model_dims,
        user,
        password,
        host,
        port,
        diskann,
        hnsw,
        minconn=1,
        maxconn=5,
        sslmode=None,
        connection_string=None,
        connection_pool=None,
    ):
        """
        Initialize the PGVector database.

        Args:
            dbname (str): Database name
            collection_name (str): Collection name
            embedding_model_dims (int): Dimension of the embedding vector
            user (str): Database user
            password (str): Database password
            host (str, optional): Database host
            port (int, optional): Database port
            diskann (bool, optional): Use DiskANN for faster search
            hnsw (bool, optional): Use HNSW for faster search
            minconn (int): Minimum number of connections to keep in the connection pool
            maxconn (int): Maximum number of connections allowed in the connection pool
            sslmode (str, optional): SSL mode for PostgreSQL connection (e.g., 'require', 'prefer', 'disable')
            connection_string (str, optional): PostgreSQL connection string (overrides individual connection parameters)
            connection_pool (Any, optional): psycopg2 connection pool object (overrides connection string and individual parameters)
        """
        self.collection_name = collection_name
        self.use_diskann = diskann
        self.use_hnsw = hnsw
        self.embedding_model_dims = embedding_model_dims
        self.connection_pool = None

        # Connection setup with priority: connection_pool > connection_string > individual parameters
        if connection_pool is not None:
            # Use provided connection pool
            self.connection_pool = connection_pool
        elif connection_string:
            if sslmode:
                # Append sslmode to connection string if provided
                if 'sslmode=' in connection_string:
                    # Replace existing sslmode
                    import re
                    connection_string = re.sub(r'sslmode=[^ ]*', f'sslmode={sslmode}', connection_string)
                else:
                    # Add sslmode to connection string
                    connection_string = f"{connection_string} sslmode={sslmode}"
        else:
            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            if sslmode:
                connection_string = f"{connection_string} sslmode={sslmode}"
        
        if self.connection_pool is None:
            if PSYCOPG_VERSION == 3:
                # psycopg3 ConnectionPool
                self.connection_pool = ConnectionPool(conninfo=connection_string, min_size=minconn, max_size=maxconn, open=True)
            else:
                # psycopg2 ThreadedConnectionPool
                self.connection_pool = ConnectionPool(minconn=minconn, maxconn=maxconn, dsn=connection_string)

        collections = self.list_cols()
        if collection_name not in collections:
            self.create_col()

    @contextmanager
    def _get_cursor(self, commit: bool = False):
        """
        Unified context manager to get a cursor from the appropriate pool.
        Auto-commits or rolls back based on exception, and returns the connection to the pool.
        """
        if PSYCOPG_VERSION == 3:
            # psycopg3 auto-manages commit/rollback and pool return
            with self.connection_pool.connection() as conn:
                with conn.cursor() as cur:
                    try:
                        yield cur
                        if commit:
                            conn.commit()
                    except Exception:
                        conn.rollback()
                        logger.error("Error in cursor context (psycopg3)", exc_info=True)
                        raise
        else:
            # psycopg2 manual getconn/putconn
            conn = self.connection_pool.getconn()
            cur = conn.cursor()
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.error(f"Error occurred: {exc}")
                raise exc
            finally:
                cur.close()
                self.connection_pool.putconn(conn)

    def create_col(self, name=None, vector_size=None, distance=None) -> None:
        """
        Create a new collection (table in PostgreSQL).
        Will also initialize vector search index if specified.
        """
        with self._get_cursor(commit=True) as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.collection_name} (
                    id BIGINT PRIMARY KEY,
                    vector vector({self.embedding_model_dims}),
                    payload JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            
            # Add created_at column if it doesn't exist (for existing tables)
            cur.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='{self.collection_name}' AND column_name='created_at') THEN
                        ALTER TABLE {self.collection_name} ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
                    END IF;
                END $$;
                """
            )

            if self.use_diskann and self.embedding_model_dims < 2000:
                cur.execute("SELECT * FROM pg_extension WHERE extname = 'vectorscale'")
                if cur.fetchone():
                    # Create DiskANN index if extension is installed for faster search
                    cur.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS {self.collection_name}_diskann_idx
                        ON {self.collection_name}
                        USING diskann (vector);
                        """
                    )
            elif self.use_hnsw:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {self.collection_name}_hnsw_idx
                    ON {self.collection_name}
                    USING hnsw (vector vector_cosine_ops)
                    """
                )

    def insert(self, vectors: list[list[float]], payloads=None, ids=None) -> list[int]:
        """
        Insert vectors into the collection.
        
        Args:
            vectors: List of vectors to insert
            payloads: List of payload dictionaries
            ids: Deprecated parameter (ignored), IDs are now generated using Snowflake algorithm
            
        Returns:
            List[int]: List of generated Snowflake IDs
        """
        logger.info(f"Inserting {len(vectors)} vectors into collection {self.collection_name}")
        if not vectors:
            return []
        
        # Generate Snowflake IDs for each vector
        generated_ids = [generate_snowflake_id() for _ in range(len(vectors))]
        
        json_payloads = [json.dumps(payload) for payload in payloads]
        # Include the generated Snowflake ID in the data
        data = [(vector_id, vector, payload) for vector_id, vector, payload in zip(generated_ids, vectors, json_payloads)]
        
        if PSYCOPG_VERSION == 3:
            with self._get_cursor(commit=True) as cur:
                # Insert with explicit IDs
                cur.executemany(
                    f"INSERT INTO {self.collection_name} (id, vector, payload) VALUES (%s, %s, %s)",
                    data,
                )
        else:
            with self._get_cursor(commit=True) as cur:
                # psycopg2: use execute_values
                execute_values(
                    cur,
                    f"INSERT INTO {self.collection_name} (id, vector, payload) VALUES %s",
                    data,
                )
        
        logger.debug(f"Successfully inserted {len(vectors)} vectors, generated Snowflake IDs: {generated_ids}")
        return generated_ids

    def search(
        self,
        query: str,
        vectors: list[float],
        limit: Optional[int] = 5,
        filters: Optional[dict] = None,
    ) -> List[OutputData]:
        """
        Search for similar vectors.

        Args:
            query (str): Query.
            vectors (List[float]): Query vector.
            limit (int, optional): Number of results to return. Defaults to 5.
            filters (Dict, optional): Filters to apply to the search. Defaults to None.

        Returns:
            list: Search results.
        """
        filter_conditions = []
        filter_params = []

        if filters:
            for k, v in filters.items():
                filter_conditions.append("payload->>%s = %s")
                filter_params.extend([k, str(v)])

        filter_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

        with self._get_cursor() as cur:
            cur.execute(
                f"""
                SELECT id, vector <=> %s::vector AS distance, payload
                FROM {self.collection_name}
                {filter_clause}
                ORDER BY distance
                LIMIT %s
                """,
                (vectors, *filter_params, limit),
            )

            results = cur.fetchall()
        return [OutputData(id=r[0], score=float(r[1]), payload=r[2]) for r in results]

    def delete(self, vector_id: int) -> None:
        """
        Delete a vector by ID.

        Args:
            vector_id (int): ID of the vector to delete.
        """
        with self._get_cursor(commit=True) as cur:
            cur.execute(f"DELETE FROM {self.collection_name} WHERE id = %s", (vector_id,))

    def update(
        self,
        vector_id: int,
        vector: Optional[list[float]] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """
        Update a vector and its payload.

        Args:
            vector_id (int): ID of the vector to update.
            vector (List[float], optional): Updated vector.
            payload (Dict, optional): Updated payload.
        """
        with self._get_cursor(commit=True) as cur:
            if vector:
               cur.execute(
                    f"UPDATE {self.collection_name} SET vector = %s WHERE id = %s",
                    (vector, vector_id),
                )
            if payload:
                # Handle JSON serialization based on psycopg version
                if PSYCOPG_VERSION == 3:
                    # psycopg3 uses psycopg.types.json.Json
                    cur.execute(
                        f"UPDATE {self.collection_name} SET payload = %s WHERE id = %s",
                        (Json(payload), vector_id),
                    )
                else:
                    # psycopg2 uses psycopg2.extras.Json
                    cur.execute(
                        f"UPDATE {self.collection_name} SET payload = %s WHERE id = %s",
                        (Json(payload), vector_id),
                    )


    def get(self, vector_id: int) -> OutputData | None:
        """
        Retrieve a vector by ID.

        Args:
            vector_id (int): ID of the vector to retrieve.

        Returns:
            OutputData: Retrieved vector.
        """
        with self._get_cursor() as cur:
            cur.execute(
                f"SELECT id, vector, payload FROM {self.collection_name} WHERE id = %s",
                (vector_id,),
            )
            result = cur.fetchone()
            if not result:
                return None
            return OutputData(id=result[0], score=None, payload=result[2])

    def list_cols(self) -> List[str]:
        """
        List all collections.

        Returns:
            List[str]: List of collection names.
        """
        with self._get_cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            return [row[0] for row in cur.fetchall()]

    def delete_col(self) -> None:
        """Delete a collection."""
        with self._get_cursor(commit=True) as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.collection_name}")

    def col_info(self) -> dict[str, Any]:
        """
        Get information about a collection.

        Returns:
            Dict[str, Any]: Collection information.
        """
        with self._get_cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    table_name,
                    (SELECT COUNT(*) FROM {self.collection_name}) as row_count,
                    (SELECT pg_size_pretty(pg_total_relation_size('{self.collection_name}'))) as total_size
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            """,
                (self.collection_name,),
            )
            result = cur.fetchone()
        return {"name": result[0], "count": result[1], "size": result[2]}

    def list(
        self,
        filters: Optional[dict] = None,
        limit: Optional[int] = 100
    ) -> List[OutputData]:
        """
        List all vectors in a collection.

        Args:
            filters (Dict, optional): Filters to apply to the list.
            limit (int, optional): Number of vectors to return. Defaults to 100.

        Returns:
            List[OutputData]: List of vectors.
        """
        filter_conditions = []
        filter_params = []

        if filters:
            for k, v in filters.items():
                filter_conditions.append("payload->>%s = %s")
                filter_params.extend([k, str(v)])

        filter_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

        query = f"""
            SELECT id, vector, payload
            FROM {self.collection_name}
            {filter_clause}
            LIMIT %s
        """

        with self._get_cursor() as cur:
            cur.execute(query, (*filter_params, limit))
            results = cur.fetchall()
        return [OutputData(id=r[0], score=None, payload=r[2]) for r in results]

    def __del__(self) -> None:
        """
        Close the database connection pool when the object is deleted.
        """
        try:
            # Close pool appropriately
            if PSYCOPG_VERSION == 3:
                self.connection_pool.close()
            else:
                self.connection_pool.closeall()
        except Exception:
            logger.error("Error closing database connection pool")
            pass

    def reset(self) -> None:
        """Reset the index by deleting and recreating it."""
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.create_col()

    def get_statistics(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get statistics for the memories in PGVector."""
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

        filter_conditions = []
        filter_params = []

        if filters:
            for k, v in filters.items():
                filter_conditions.append("payload->>%s = %s")
                filter_params.extend([k, str(v)])

        filter_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

        with self._get_cursor() as cur:
            # Get total count
            cur.execute(f"SELECT COUNT(*) FROM {self.collection_name} {filter_clause}", filter_params)
            stats["total_memories"] = cur.fetchone()[0]

            if stats["total_memories"] == 0:
                return stats

            # Get distribution by type
            cur.execute(
                f"""
                SELECT 
                    COALESCE(payload->>'category', payload->>'type', 'unknown') as type, 
                    COUNT(*) 
                FROM {self.collection_name} 
                {filter_clause}
                GROUP BY type
                """,
                filter_params
            )
            for row in cur.fetchall():
                stats["by_type"][row[0]] = row[1]

            # Get average importance
            cur.execute(
                f"""
                SELECT AVG(CAST(COALESCE(payload->>'importance', payload->'metadata'->>'importance', '0') AS FLOAT))
                FROM {self.collection_name}
                {filter_clause}
                """,
                filter_params
            )
            stats["avg_importance"] = round(float(cur.fetchone()[0] or 0.0), 2)

            # Get top accessed
            cur.execute(
                f"""
                SELECT id, payload->>'data' as content, 
                       CAST(COALESCE(payload->>'access_count', payload->'metadata'->>'access_count', '0') AS INTEGER) as access_count
                FROM {self.collection_name}
                {filter_clause}
                ORDER BY access_count DESC
                LIMIT 10
                """,
                filter_params
            )
            for row in cur.fetchall():
                stats["top_accessed"].append({
                    "id": row[0],
                    "content": (row[1] or "")[:50],
                    "access_count": row[2]
                })

            # Growth trend
            cur.execute(
                f"""
                SELECT DATE(created_at) as date, COUNT(*)
                FROM {self.collection_name}
                {filter_clause}
                GROUP BY date
                ORDER BY date
                """,
                filter_params
            )
            for row in cur.fetchall():
                stats["growth_trend"][str(row[0])] = row[1]

            # Age distribution
            cur.execute(
                f"""
                SELECT 
                    CASE 
                        WHEN created_at > NOW() - INTERVAL '1 day' THEN '< 1 day'
                        WHEN created_at > NOW() - INTERVAL '7 days' THEN '1-7 days'
                        WHEN created_at > NOW() - INTERVAL '30 days' THEN '7-30 days'
                        ELSE '> 30 days'
                    END as age_bucket,
                    COUNT(*)
                FROM {self.collection_name}
                {filter_clause}
                GROUP BY age_bucket
                """,
                filter_params
            )
            for row in cur.fetchall():
                if row[0] in stats["age_distribution"]:
                    stats["age_distribution"][row[0]] = row[1]

        return stats

    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs from PGVector."""
        query = f"SELECT DISTINCT payload->>'user_id' FROM {self.collection_name} WHERE payload ? 'user_id'"
        with self._get_cursor() as cur:
            cur.execute(query)
            return [str(row[0]) for row in cur.fetchall() if row[0] is not None]
