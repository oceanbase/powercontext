"""
OceanBase storage implementation

This module provides OceanBase-based storage for memory data.
"""
import heapq
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase, OutputData
from powermem.utils.utils import serialize_datetime, generate_snowflake_id
from powermem.utils.oceanbase_util import OceanBaseUtil

try:
    from pyobvector import (
        VECTOR,
        SPARSE_VECTOR,
        ObVecClient,
        cosine_distance,
        inner_product,
        l2_distance,
        VecIndexType,
        FtsIndexParam,
        FtsParser,
)
    from pyobvector.schema import ReplaceStmt
    from sqlalchemy import JSON, Column, String, Table, func, ColumnElement, BigInteger
    from sqlalchemy import text, and_, or_, not_, select, bindparam, literal_column
    from sqlalchemy.dialects.mysql import LONGTEXT
except ImportError as e:
    raise ImportError(
        f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy."
    )

from powermem.storage.oceanbase import constants
from .models import create_memory_model

logger = logging.getLogger(__name__)

class OceanBaseVectorStore(VectorStoreBase):
    """OceanBase vector store implementation"""

    def __init__(
            self,
            collection_name: str,
            connection_args: Optional[Dict[str, Any]] = None,
            vidx_metric_type: str = constants.DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE,
            vidx_algo_params: Optional[Dict] = None,
            index_type: str = constants.DEFAULT_INDEX_TYPE,
            embedding_model_dims: Optional[int] = None,
            primary_field: str = constants.DEFAULT_PRIMARY_FIELD,
            vector_field: str = constants.DEFAULT_VECTOR_FIELD,
            text_field: str = constants.DEFAULT_TEXT_FIELD,
            metadata_field: str = constants.DEFAULT_METADATA_FIELD,
            vidx_name: str = constants.DEFAULT_VIDX_NAME,
            normalize: bool = False,
            include_sparse: bool = False,
            auto_configure_vector_index: bool = True,
            # Connection parameters (for compatibility with config)
            host: Optional[str] = None,
            port: Optional[str] = None,
            user: Optional[str] = None,
            password: Optional[str] = None,
            db_name: Optional[str] = None,
            hybrid_search: bool = True,
            fulltext_parser: str = constants.DEFAULT_FULLTEXT_PARSER,
            vector_weight: float = 0.5,
            fts_weight: float = 0.5,
            sparse_weight: float = 0.25,
            reranker: Optional[Any] = None,
            **kwargs,
    ):
        """
        Initialize the OceanBase vector store.

        Args:
            collection_name (str): Name of the collection/table.
            connection_args (Optional[Dict[str, Any]]): Connection parameters for OceanBase.
            vidx_metric_type (str): Metric method of distance between vectors.
            vidx_algo_params (Optional[Dict]): Index parameters.
            index_type (str): Type of vector index to use.
            embedding_model_dims (Optional[int]): Dimension of vectors.
            primary_field (str): Name of the primary key column.
            vector_field (str): Name of the vector column.
            text_field (str): Name of the text column.
            metadata_field (str): Name of the metadata column.
            vidx_name (str): Name of the vector index.
            normalize (bool): Whether to perform L2 normalization on vectors.
            include_sparse (bool): Whether to include sparse vector support.
            auto_configure_vector_index (bool): Whether to automatically configure vector index settings.
            host (Optional[str]): OceanBase server host.
            port (Optional[str]): OceanBase server port.
            user (Optional[str]): OceanBase username.
            password (Optional[str]): OceanBase password.
            db_name (Optional[str]): OceanBase database name.
            hybrid_search (bool): Whether to use hybrid search.
            vector_weight (float): Weight for vector search in hybrid search (default: 0.5).
            fts_weight (float): Weight for full-text search in hybrid search (default: 0.5).
            sparse_weight (Optional[float]): Weight for sparse vector search in hybrid search.
            reranker (Optional[Any]): Reranker model for fine ranking.
        """
        self.normalize = normalize
        self.include_sparse = include_sparse
        self.auto_configure_vector_index = auto_configure_vector_index
        self.hybrid_search = hybrid_search
        self.fulltext_parser = fulltext_parser
        self.vector_weight = vector_weight
        self.fts_weight = fts_weight
        self.sparse_weight = sparse_weight
        self.reranker = reranker

        # Validate fulltext parser
        if self.fulltext_parser not in constants.OCEANBASE_SUPPORTED_FULLTEXT_PARSERS:
            supported = ', '.join(constants.OCEANBASE_SUPPORTED_FULLTEXT_PARSERS)
            raise ValueError(
                f"Invalid fulltext parser: {self.fulltext_parser}. "
                f"Supported parsers are: {supported}"
            )

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

        self.index_type = index_type.upper()
        if self.index_type not in constants.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES:
            raise ValueError(
                f"`index_type` should be one of "
                f"{list(constants.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES.keys())}. "
                f"Got {self.index_type}"
            )

        # Set default parameters based on index type
        if vidx_algo_params is None:
            index_param_map = constants.OCEANBASE_BUILD_PARAMS_MAPPING
            self.vidx_algo_params = index_param_map[self.index_type].copy()

            if self.index_type == "IVF_PQ" and "m" not in self.vidx_algo_params:
                self.vidx_algo_params["m"] = 3
        else:
            self.vidx_algo_params = vidx_algo_params.copy()

        # Set field names
        self.collection_name = collection_name
        self.embedding_model_dims = int(embedding_model_dims) if embedding_model_dims is not None else None
        self.primary_field = primary_field
        self.vector_field = vector_field
        self.text_field = text_field
        self.metadata_field = metadata_field
        self.vidx_name = vidx_name
        self.sparse_vector_field = "sparse_embedding"
        self.fulltext_field = "fulltext_content"

        # Set up vector index parameters
        self.vidx_metric_type = vidx_metric_type.lower()

        # Initialize client
        self._create_client(**kwargs)
        assert self.obvector is not None

        # Autoconfigure vector index settings if enabled
        if self.auto_configure_vector_index:
            self._configure_vector_index_settings()

        self._create_col()

    def _create_client(self, **kwargs):
        """Create and initialize the OceanBase vector client."""
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

    def _configure_vector_index_settings(self):
        """Configure OceanBase vector index settings automatically."""
        try:
            logger.info("Configuring OceanBase vector index settings...")
            # Check if it's seekdb
            if OceanBaseUtil.is_seekdb(self.obvector):
                logger.info("seekdb is adaptive mode, no need to configure sparse vector")
                return

            # Check if it's OceanBase and version >= 4.5.0
            version_dict = OceanBaseUtil.get_version_number(self.obvector)
            if version_dict is None:
                logger.warning("Could not determine database version, please check the database version")
                return

            major = version_dict["major"]
            minor = version_dict["minor"]
            patch = version_dict["patch"]

            if major >= 4 and minor >= 4 and patch >= 1:
                logger.info("OceanBase version >= 4.4.1, no need to configure sparse vector")
                return

            # Set vector memory limit percentage
            with self.obvector.engine.connect() as conn:
                # Check if ob_vector_memory_limit_percentage is already set
                result = conn.execute(text("SHOW PARAMETERS LIKE 'ob_vector_memory_limit_percentage'"))
                row = result.fetchone()
                
                if row:
                    logger.info(f"ob_vector_memory_limit_percentage already set, skipping configuration")
                    return
                else:
                    conn.execute(text("ALTER SYSTEM SET ob_vector_memory_limit_percentage = 30"))
                    conn.commit()
                    logger.info("Set ob_vector_memory_limit_percentage = 30 successfully")

            logger.info("OceanBase vector index configuration completed")

        except Exception as e:
            logger.warning(f"Failed to configure vector index settings: {e}")
            logger.warning("   Vector index functionality may not work properly")

    def _create_table_with_index_by_embedding_model_dims(self) -> None:
        """Create table with vector index based on embedding dimension.
        
        If include_sparse is True and database supports sparse vector,
        the sparse_embedding column will be included in the table schema.
        """
        cols = [
            # Primary key - Snowflake ID (BIGINT without AUTO_INCREMENT)
            Column(self.primary_field, BigInteger, primary_key=True, autoincrement=False),
            # Vector field
            Column(self.vector_field, VECTOR(self.embedding_model_dims)),
            # Text content field
            Column(self.text_field, LONGTEXT),
            # Metadata field (JSON)
            Column(self.metadata_field, JSON),
            Column("user_id", String(128)),  # User identifier
            Column("agent_id", String(128)),  # Agent identifier
            Column("run_id", String(128)),  # Run identifier
            Column("actor_id", String(128)),  # Actor identifier
            Column("hash", String(32)),  # MD5 hash (32 chars)
            Column("created_at", String(128)),
            Column("updated_at", String(128)),
            Column("category", String(64)),  # Category name
            Column(self.fulltext_field, LONGTEXT)
        ]

        # Create vector index parameters
        vidx_params = self.obvector.prepare_index_params()
        
        # Add dense vector index
        vidx_params.add_index(
            field_name=self.vector_field,
            index_type=constants.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES[self.index_type],
            index_name=self.vidx_name,
            metric_type=self.vidx_metric_type,
            params=self.vidx_algo_params,
        )

        fts_index_param = None
        if self.hybrid_search:
            # Convert string parser name to FtsParser enum
            parser_enum = OceanBaseUtil.get_fts_parser_enum(self.fulltext_parser)
            fts_index_param = FtsIndexParam(
                index_name=f"fulltext_index_for_col_text",
                field_names=[self.fulltext_field],
                parser_type=parser_enum,
            )
            logger.debug(f"Added fulltext index configuration with parser '{self.fulltext_parser}' for table '{self.collection_name}'")

        if self.include_sparse:
            if OceanBaseUtil.check_sparse_vector_version_support(self.obvector):
                cols.append(Column(self.sparse_vector_field, SPARSE_VECTOR))
                logger.info(f"Including sparse_embedding column in new table '{self.collection_name}'")
                vidx_params.add_index(
                    field_name=self.sparse_vector_field,
                    index_type="daat",
                    index_name="sparse_embedding_idx",
                    metric_type="inner_product",
                    sparse_index_type="sindi",  # use sindi index type
                )
                logger.debug(f"Added sparse vector index configuration for table '{self.collection_name}'")
            else:
                logger.warning(
                    "Database does not support SPARSE_VECTOR type. "
                    "Creating table without sparse vector support. "
                    "Upgrade to seekdb or OceanBase >= 4.5.0 for sparse vector."
                )
        
        # Create table with vector indexes (both dense and sparse if configured)
        self.obvector.create_table_with_index_params(
            table_name=self.collection_name,
            columns=cols,
            indexes=None,
            vidxs=vidx_params,
            fts_idxs=[fts_index_param] if fts_index_param is not None else None,
            partitions=None,
        )

        logger.debug(f"Table '{self.collection_name}' created successfully")

    def _get_distance_function(self, metric_type: str):
        """Get the appropriate distance function for the given metric type."""
        if metric_type == "inner_product":
            return inner_product
        elif metric_type == "l2":
            return l2_distance
        elif metric_type == "cosine":
            return cosine_distance
        else:
            raise ValueError(f"Unsupported metric type: {metric_type}")

    def _get_default_search_params(self) -> dict:
        """Get default search parameters based on index type."""
        search_param_map = constants.OCEANBASE_SEARCH_PARAMS_MAPPING
        return search_param_map.get(
            self.index_type, constants.DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM
        )

    def create_col(self, name: str, vector_size: Optional[int] = None, distance: str = "l2"):
        """Create a new collection."""
        try:
            if vector_size is None:
                raise ValueError("vector_size must be specified to create a collection.")
            distance = distance.lower()
            if distance not in ("l2", "inner_product", "cosine"):
                raise ValueError("distance must be one of 'l2', 'inner_product', or 'cosine'.")
            self.embedding_model_dims = int(vector_size)
            self.vidx_metric_type = distance
            self.collection_name = name

            self._create_col()
            logger.info(f"Successfully created collection '{name}' with vector size {vector_size} and distance '{distance}'")
            
        except ValueError as e:
            logger.error(f"Invalid parameters for creating collection: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to create collection '{name}': {e}", exc_info=True)
            raise

    def _create_col(self):
        """Create a new collection."""
        
        if self.embedding_model_dims is None:
            raise ValueError(
                "embedding_model_dims is required for OceanBase vector operations. "
                "Please configure embedding_model_dims in your OceanBaseConfig."
            )

        # Set up vector index parameters
        if self.vidx_metric_type not in ("l2", "inner_product", "cosine"):
            raise ValueError(
                "`vidx_metric_type` should be set in `l2`/`inner_product`/`cosine`."
            )

        # Only create table if it doesn't exist (preserve existing data)
        if not self.obvector.check_table_exists(self.collection_name):
            # New table: create with full schema (including sparse_embedding if enabled)
            self._create_table_with_index_by_embedding_model_dims()
            logger.info(f"Created new table {self.collection_name}")
        else:
            # Existing table: validate schema
            logger.info(f"Table {self.collection_name} already exists, preserving existing data")
            
            # Check if the existing table's vector dimension matches the requested dimension
            existing_dim = self._get_existing_vector_dimension()
            if existing_dim is not None and existing_dim != self.embedding_model_dims:
                raise ValueError(
                    f"Vector dimension mismatch: existing table '{self.collection_name}' has "
                    f"vector dimension {existing_dim}, but requested dimension is {self.embedding_model_dims}. "
                    f"Please use a different collection name or delete the existing table."
                )

        if self.hybrid_search:
            self._check_and_create_fulltext_index()
        
        # Validate sparse vector support if enabled
        if self.include_sparse:
            if not OceanBaseUtil.check_sparse_vector_ready(self.obvector, self.collection_name, self.sparse_vector_field):
                self.include_sparse = False
        
        self.model_class = create_memory_model(
            table_name=self.collection_name,
            embedding_dims=self.embedding_model_dims,
            include_sparse=self.include_sparse,
            primary_field=self.primary_field,
            vector_field=self.vector_field,
            text_field=self.text_field,
            metadata_field=self.metadata_field,
            fulltext_field=self.fulltext_field,
            sparse_vector_field=self.sparse_vector_field
        )
        
        # Use model_class.__table__ as table reference
        self.table = self.model_class.__table__

    def insert(self,
               vectors: List[List[float]],
               payloads: Optional[List[Dict]] = None,
               ids: Optional[List[str]] = None) -> List[int]:
        """
        Insert vectors into the collection.
        
        Args:
            vectors: List of vectors to insert
            payloads: Optional list of payload dictionaries
            ids: Deprecated parameter (ignored), IDs are now generated using Snowflake algorithm
            
        Returns:
            List[int]: List of generated Snowflake IDs
        """
        try:
            if not vectors:
                return []

            if payloads is None:
                payloads = [{} for _ in vectors]

            # Generate Snowflake IDs for each vector
            generated_ids = [generate_snowflake_id() for _ in range(len(vectors))]

            # Prepare data for insertion with explicit IDs
            data: List[Dict[str, Any]] = []
            for vector, payload, vector_id in zip(vectors, payloads, generated_ids):
                record = self._build_record_for_insert(vector, payload)
                # Explicitly set the primary key field with Snowflake ID
                record[self.primary_field] = vector_id
                data.append(record)

            # Use transaction to ensure atomicity of insert
            with self.obvector.engine.connect() as conn:
                with conn.begin():
                    # Execute REPLACE INTO (upsert) statement
                    upsert_stmt = ReplaceStmt(self.table).values(data)
                    conn.execute(upsert_stmt)
            
            logger.debug(f"Successfully inserted {len(vectors)} vectors, generated Snowflake IDs: {generated_ids}")
            return generated_ids
            
        except Exception as e:
            logger.error(f"Failed to insert vectors into collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def _generate_where_clause(self, filters: Optional[Dict] = None, table = None) -> Optional[List]:
        """
        Generate a properly formatted where clause for OceanBase.

        Args:
            filters (Optional[Dict]): The filter conditions.
                Supports both simple and complex formats:

                Simple format (Open Source):
                - Simple values: {"field": "value"} -> field = 'value'
                - Comparison ops: {"field": {"gte": 10, "lte": 20}}
                - List values: {"field": ["a", "b", "c"]} -> field IN ('a', 'b', 'c')

                Complex format (Platform):
                - AND logic: {"AND": [{"user_id": "alice"}, {"category": "food"}]}
                - OR logic: {"OR": [{"rating": {"gte": 4.0}}, {"priority": "high"}]}
                - Nested: {"AND": [{"user_id": "alice"}, {"OR": [{"rating": {"gte": 4.0}}, {"priority": "high"}]}]}
            table: SQLAlchemy Table object to use for column references. If None, uses self.table.

        Returns:
            Optional[List]: List of SQLAlchemy ColumnElement objects for where clause.
        """
        # Use provided table or fall back to self.table
        if table is None:
            table = self.table

        def get_column(key) -> ColumnElement:
            """Get the appropriate column element for a field."""
            if key in table.c:
                return table.c[key]
            else:
                # Use ->> operator for unquoted JSON extract (MySQL/PostgreSQL)
                return table.c[self.metadata_field].op("->>")(f"$.{key}")

        def build_condition(key, value):
            """Build a single condition."""
            column = get_column(key)

            if isinstance(value, dict):
                # Handle comparison operators
                conditions = []
                for op, op_value in value.items():
                    op = op.lstrip("$")
                    match op:
                        case "eq":
                            conditions.append(column == op_value)
                        case "ne":
                            conditions.append(column != op_value)
                        case "gt":
                            conditions.append(column > op_value)
                        case "gte":
                            conditions.append(column >= op_value)
                        case "lt":
                            conditions.append(column < op_value)
                        case "lte":
                            conditions.append(column <= op_value)
                        case "in":
                            if not isinstance(op_value, list):
                                raise TypeError(f"Value for $in must be a list, got {type(op_value)}")
                            conditions.append(column.in_(op_value))
                        case "nin":
                            if not isinstance(op_value, list):
                                raise TypeError(f"Value for $nin must be a list, got {type(op_value)}")
                            conditions.append(~column.in_(op_value))
                        case "like":
                            conditions.append(column.like(str(op_value)))
                        case "ilike":
                            conditions.append(column.ilike(str(op_value)))
                        case _:
                            raise ValueError(f"Unsupported operator: {op}")
                return and_(*conditions) if conditions else None
            elif value is None:
                return column.is_(None)
            else:
                return column == value

        def process_condition(cond):
            """Process a single condition, handling nested AND/OR logic."""
            if isinstance(cond, dict):
                # Handle complex filters with AND/OR
                if "AND" in cond:
                    and_conditions = [process_condition(item) for item in cond["AND"]]
                    and_conditions = [c for c in and_conditions if c is not None]
                    return and_(*and_conditions) if and_conditions else None
                elif "OR" in cond:
                    or_conditions = [process_condition(item) for item in cond["OR"]]
                    or_conditions = [c for c in or_conditions if c is not None]
                    return or_(*or_conditions) if or_conditions else None
                else:
                    # Simple key-value filters
                    conditions = []
                    for k, v in cond.items():
                        expr = build_condition(k, v)
                        if expr is not None:
                            conditions.append(expr)
                    return and_(*conditions) if conditions else None
            elif isinstance(cond, list):
                subconditions = [process_condition(c) for c in cond]
                subconditions = [c for c in subconditions if c is not None]
                return and_(*subconditions) if subconditions else None
            else:
                return None

        # Handle complex filters with AND/OR
        result = process_condition(filters)
        return [result] if result is not None else None

    def _row_to_model(self, row):
        """
        Convert SQLAlchemy Row object to ORM Model instance.
        
        Args:
            row: SQLAlchemy Row object (query result)
        
        Returns:
            Model instance, accessible via attributes (record.document, record.id, etc.)
        """
        # Create a new Model instance (not bound to Session)
        record = self.model_class()
        
        # Iterate through all columns in the table, map values from Row to Model instance
        for col_name in self.model_class.__table__.c.keys():
            # Check if Row contains this column (queries may not include all columns)
            if col_name in row._mapping.keys():
                attr_name = 'metadata_' if col_name == 'metadata' else col_name
                setattr(record, attr_name, row._mapping[col_name])
        
        return record

    def _get_standard_select_columns(self) -> List:
        """
        Get the standard column list for SELECT queries.
        """
        columns = [
            self.table.c[self.text_field],
            self.table.c[self.metadata_field],
            self.table.c[self.primary_field],
            self.table.c["user_id"],
            self.table.c["agent_id"],
            self.table.c["run_id"],
            self.table.c["actor_id"],
            self.table.c["hash"],
            self.table.c["created_at"],
            self.table.c["updated_at"],
            self.table.c["category"],
        ]
        
        # Only include sparse_embedding if sparse search is enabled
        if self.include_sparse:
            columns.append(self.table.c[self.sparse_vector_field])
        
        return columns

    def _get_standard_column_names(self, include_vector_field: bool = False) -> List[str]:
        """
        Get the standard column name list for obvector output_column_names parameter.
        """
        column_names = [
            self.text_field,
        ]
        
        # Include vector_field if requested
        if include_vector_field:
            column_names.append(self.vector_field)
        
        column_names.extend([
            self.metadata_field,
            self.primary_field,
            "user_id",
            "agent_id",
            "run_id",
            "actor_id",
            "hash",
            "created_at",
            "updated_at",
            "category",
        ])
        
        # Only include sparse_embedding if sparse search is enabled
        if self.include_sparse:
            column_names.append(self.sparse_vector_field)
        
        return column_names

    def _parse_row_to_dict(self, row, include_vector: bool = False, extract_score: bool = True) -> Dict:
        """
        Parse a database row and return all fields as a dictionary.
        Now uses ORM Model instance internally for cleaner field access.
        
        Args:
            row: Database row result
            include_vector: Whether the row includes vector field (for get/list methods)
            extract_score: Whether to extract score/distance field (for search methods)
        
        Returns:
            Dict containing all parsed fields:
            - text_content: Text content from the row
            - metadata_json: Raw metadata JSON string
            - vector_id: Vector ID
            - vector: Vector data (only if include_vector=True)
            - user_id, agent_id, run_id, actor_id, hash_val: Standard fields
            - created_at, updated_at, category: Timestamp and category fields
            - metadata: Built metadata dictionary
            - score_or_distance: Score or distance value (only if extract_score=True)
        """
        record = self._row_to_model(row)
        
        text_content = record.document
        metadata_json = record.metadata_
        vector_id = record.id
        user_id = record.user_id
        agent_id = record.agent_id
        run_id = record.run_id
        actor_id = record.actor_id
        hash_val = record.hash
        created_at = record.created_at
        updated_at = record.updated_at
        category = record.category
        
        # Handle optional fields
        vector = None
        sparse_embedding = None
        score_or_distance = None
        
        if include_vector:
            # get/list scenario: includes vector field
            vector = record.embedding
            if self.include_sparse and hasattr(record, 'sparse_embedding') and record.sparse_embedding is not None:
                sparse_embedding = record.sparse_embedding
        else:
            # Search scenario: does not include vector, but may include sparse_embedding
            if self.include_sparse and hasattr(record, 'sparse_embedding') and record.sparse_embedding is not None:
                sparse_embedding = record.sparse_embedding
        
        # Extract additional score/distance fields (these fields are not in Model, need to get from original row)
        if extract_score:
            if 'score' in row._mapping.keys():
                score_or_distance = row._mapping['score']
            elif 'distance' in row._mapping.keys():
                score_or_distance = row._mapping['distance']
            elif 'anon_1' in row._mapping.keys():
                score_or_distance = row._mapping['anon_1']
        
        # Build standard metadata
        metadata = {
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "actor_id": actor_id,
            "hash": hash_val,
            "created_at": created_at,
            "updated_at": updated_at,
            "category": category,
            # Store user metadata as nested structure to preserve it
            "metadata": OceanBaseUtil.parse_metadata(metadata_json)
        }
        
        # Build result dictionary
        result = {
            "text_content": text_content,
            "metadata_json": metadata_json,
            "vector_id": vector_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "actor_id": actor_id,
            "hash_val": hash_val,
            "created_at": created_at,
            "updated_at": updated_at,
            "category": category,
            "metadata": metadata,
        }
        
        # Add optional fields
        if include_vector:
            result["vector"] = vector
        
        if extract_score:
            result["score_or_distance"] = score_or_distance
        
        return result

    def _create_output_data(self, vector_id: int, text_content: str, score: float,
                            metadata: Dict) -> OutputData:
        """Create an OutputData object with standard structure."""
        return OutputData(
            id=vector_id,
            score=score,
            payload={
                "data": text_content,
                **metadata
            }
        )

    def _build_record_for_insert(self, vector: List[float], payload: Dict) -> Dict[str, Any]:
        """
        Build a record dictionary for insertion with all standard fields.
        Note: Primary key (id) should be set explicitly before insertion.
        """
        # Serialize metadata to handle datetime objects
        metadata = payload.get("metadata", {})
        serialized_metadata = serialize_datetime(metadata) if metadata else {}
        
        record = {
            # Primary key (id) will be set explicitly in insert() method with Snowflake ID
            self.vector_field: (
                vector if not self.normalize else OceanBaseUtil.normalize(vector)
            ),
            self.text_field: payload.get("data", ""),
            self.metadata_field: serialized_metadata,
            "user_id": payload.get("user_id", ""),
            "agent_id": payload.get("agent_id", ""),
            "run_id": payload.get("run_id", ""),
            "actor_id": payload.get("actor_id", ""),
            "hash": payload.get("hash", ""),
            "created_at": serialize_datetime(payload.get("created_at", "")),
            "updated_at": serialize_datetime(payload.get("updated_at", "")),
            "category": payload.get("category", ""),
        }

        # Add hybrid search fields if enabled
        if self.include_sparse and "sparse_embedding" in payload:
            sparse_embedding = payload["sparse_embedding"]
            # pyobvector's SparseVector type expects a dict format, not a string
            if isinstance(sparse_embedding, dict):
                record[self.sparse_vector_field] = sparse_embedding
            else:
                raise ValueError(f"Sparse embedding must be a dict, got {type(sparse_embedding)}")

        # Always add full-text content (enabled by default)
        fulltext_content = payload.get("fulltext_content") or payload.get("data", "")
        record[self.fulltext_field] = fulltext_content

        return record

    def search(self,
               query: str,
               vectors: List[List[float]],
               limit: int = 5,
               filters: Optional[Dict] = None,
               sparse_embedding: Optional[Dict[int, float]] = None) -> list[OutputData]:
        # Check if hybrid search is enabled, and we have query text
        # Full-text search is always enabled by default
        if self.hybrid_search and query:
            return self._hybrid_search(query, vectors, limit, filters, sparse_embedding)
        else:
            return self._vector_search(query, vectors, limit, filters)

    def _vector_search(self,
                       query: str,
                       vectors: List[List[float]],
                       limit: int = 5,
                       filters: Optional[Dict] = None) -> list[OutputData]:
        """Perform pure vector search."""
        try:
            # Handle both cases: single vector or list of vectors
            # If vectors is a single vector (list of floats), use it directly
            if isinstance(vectors, list) and len(vectors) > 0 and isinstance(vectors[0], (int, float)):
                query_vector = vectors
            # If vectors is a list of vectors, use the first one
            elif isinstance(vectors, list) and len(vectors) > 0 and isinstance(vectors[0], list):
                query_vector = vectors[0]
            else:
                logger.warning("Invalid vector format provided for search")
                return []

            table = Table(self.collection_name, self.obvector.metadata_obj, autoload_with=self.obvector.engine)
            
            # Build where clause from filters using the same table object
            where_clause = self._generate_where_clause(filters, table=table)

            # Build output column names list
            output_columns = self._get_standard_column_names()
            
            # Perform vector search - pyobvector expects a single vector, not a list of vectors
            results = self.obvector.ann_search(
                table_name=self.collection_name,
                vec_data=query_vector if not self.normalize else OceanBaseUtil.normalize(query_vector),
                vec_column_name=self.vector_field,
                distance_func=self._get_distance_function(self.vidx_metric_type),
                with_dist=True,
                topk=limit,
                output_column_names=output_columns,
                where_clause=where_clause,
            )

            # Convert results to OutputData objects
            search_results = []
            for row in results.fetchall():
                parsed = self._parse_row_to_dict(row, include_vector=False, extract_score=True)
                
                # Convert distance to similarity score (0-1 range, higher is better)
                # Handle None distance (shouldn't happen but be defensive)
                distance = parsed["score_or_distance"]
                vector_id = parsed["vector_id"]
                text_content = parsed["text_content"]
                metadata = parsed["metadata"]
                
                if distance is None:
                    logger.warning(f"Distance is None for vector_id {vector_id}, using default similarity 0.0")
                    similarity = 0.0
                elif self.vidx_metric_type == "l2":
                    # For L2 distance, lower is better. Convert to similarity: 1/(1+distance)
                    similarity = 1.0 / (1.0 + float(distance))
                elif self.vidx_metric_type == "cosine":
                    # For cosine distance (range 0-2), lower is better. Convert to similarity: 1 - distance/2
                    # This maps distance 0->1.0, distance 2->0.0
                    similarity = max(0.0, 1.0 - float(distance) / 2.0)
                elif self.vidx_metric_type == "inner_product":
                    # For inner product, higher is better (returns negative distance)
                    # For normalized vectors, inner product is in range [-1, 1]
                    # Convert to similarity: (inner_product + 1) / 2
                    inner_prod = -float(distance)  # Negate to get actual inner product
                    similarity = (inner_prod + 1.0) / 2.0
                    similarity = max(0.0, min(1.0, similarity))  # Clamp to [0, 1]
                else:
                    # Unknown metric, use default
                    similarity = 0.0
                
                # Store original similarity in metadata
                metadata['_vector_similarity'] = similarity
                
                # For pure vector search (no fusion), quality score equals vector similarity
                metadata['_quality_score'] = similarity

                search_results.append(self._create_output_data(vector_id, text_content, similarity, metadata))
            logger.debug(f"_vector_search results, len : {len(search_results)}")
            return search_results
            
        except Exception as e:
            logger.error(f"Vector search failed in collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def _fulltext_search(self, query: str, limit: int = 5, filters: Optional[Dict] = None) -> list[OutputData]:
        """Perform full-text search using OceanBase FTS with parameterized queries including score."""
        # Skip search if query is empty
        if not query or not query.strip():
            logger.debug("Full-text search query is empty, returning empty results.")
            return []
        
        # Generate where clause from filters using the existing method
        filter_where_clause = self._generate_where_clause(filters)

        # Build the full-text search condition using SQLAlchemy text with parameters
        # Use the same parameter format that SQLAlchemy will use for other parameters
        fts_condition = text(f"MATCH({self.fulltext_field}) AGAINST(:query IN NATURAL LANGUAGE MODE)").bindparams(
            bindparam("query", query)
        )

        # Combine FTS condition with filter conditions
        where_conditions = [fts_condition]
        if filter_where_clause:
            where_conditions.extend(filter_where_clause)

        # Build custom query to include score field
        try:
            # Build select statement with specific columns AND score
            columns = self._get_standard_select_columns() + [
                # Add the score calculation as a column
                text(f"MATCH({self.fulltext_field}) AGAINST(:query IN NATURAL LANGUAGE MODE) as score").bindparams(
                    bindparam("query", query)
                )
            ]

            stmt = select(*columns)

            # Add where conditions
            for condition in where_conditions:
                stmt = stmt.where(condition)

            # Order by score DESC to get best matches first
            stmt = stmt.order_by(text('score DESC'))

            # Add limit
            if limit:
                stmt = stmt.limit(limit)

            # Execute the query with parameters - use direct parameter passing
            with self.obvector.engine.connect() as conn:
                with conn.begin():
                    logger.info(f"Executing FTS query with parameters: query={query}")
                    # Execute with parameter dictionary - the standard SQLAlchemy way
                    results = conn.execute(stmt)
                    rows = results.fetchall()

        except Exception as e:
            logger.warning(f"Full-text search failed, falling back to LIKE search: {e}")
            try:
                # Fallback to simple LIKE search with parameters
                like_query = f"%{query}%"
                like_condition = text(f"{self.fulltext_field} LIKE :like_query").bindparams(
                    bindparam("like_query", like_query)
                )

                fallback_conditions = [like_condition]
                if filter_where_clause:
                    fallback_conditions.extend(filter_where_clause)

                # Build fallback query with default score
                columns = self._get_standard_select_columns() + [
                    # Default score for LIKE search
                    '1.0 as score'
                ]

                stmt = select(*columns)

                for condition in fallback_conditions:
                    stmt = stmt.where(condition)

                if limit:
                    stmt = stmt.limit(limit)

                # Execute fallback query with parameters
                with self.obvector.engine.connect() as conn:
                    with conn.begin():
                        logger.info(f"Executing LIKE fallback query with parameters: like_query={like_query}")
                        # Execute with parameter dictionary - the standard SQLAlchemy way
                        results = conn.execute(stmt)
                        rows = results.fetchall()
            except Exception as fallback_error:
                logger.error(f"Both full-text search and LIKE fallback failed: {fallback_error}")
                return []

        # Convert results to OutputData objects
        fts_results = []
        for row in rows:
            parsed = self._parse_row_to_dict(row, include_vector=False, extract_score=True)
            
            # FTS score is already in 0-1 range (higher is better)
            fts_score = float(parsed["score_or_distance"])
            
            # Store original similarity in metadata
            metadata = parsed["metadata"]
            metadata['_fts_score'] = fts_score
            
            fts_results.append(self._create_output_data(
                parsed["vector_id"], 
                parsed["text_content"], 
                fts_score, 
                metadata
            ))

        logger.info(f"_fulltext_search results, len : {len(fts_results)}, fts_results : {fts_results}")
        return fts_results

    def _sparse_search(self, sparse_embedding: Dict[int, float], limit: int = 5, filters: Optional[Dict] = None) -> list[OutputData]:
        """
        Perform sparse vector search using OceanBase SPARSEVECTOR.
        
        Args:
            sparse_embedding: Sparse embedding dictionary (token_id -> weight)
            limit: Maximum number of results to return
            filters: Optional filter conditions
            
        Returns:
            List of OutputData objects with search results
        """
        # Check if sparse search is enabled
        if not self.include_sparse:
            logger.debug("Sparse vector search is not enabled")
            return []
        
        # Check if sparse embedding is provided
        if not sparse_embedding or not isinstance(sparse_embedding, dict):
            logger.debug("Sparse embedding not provided, skipping sparse search")
            return []
        
        try:
            
            # Format sparse vector for SQL query
            sparse_vector_str = OceanBaseUtil.format_sparse_vector(sparse_embedding)
            
            # Generate where clause from filters
            filter_where_clause = self._generate_where_clause(filters)
            
            # Build the sparse vector search query
            # Use negative_inner_product for ordering (lower is better, so we negate)
            columns = self._get_standard_select_columns() + [
                # Add the distance calculation as a column (negative_inner_product)
                # Directly embed sparse_vector_str in SQL as per OceanBase syntax
                text(f"negative_inner_product({self.sparse_vector_field}, '{sparse_vector_str}') as score")
            ]
            
            stmt = select(*columns)
            
            # Add where conditions
            if filter_where_clause:
                for condition in filter_where_clause:
                    stmt = stmt.where(condition)
            
            # Order by score ASC (lower negative_inner_product means higher similarity)
            stmt = stmt.order_by(text('score ASC'))
            
            # Add APPROXIMATE LIMIT (using regular LIMIT as APPROXIMATE may not be supported in all versions)
            if limit:
                stmt = stmt.limit(limit)
            
            # Execute the query
            with self.obvector.engine.connect() as conn:
                with conn.begin():
                    logger.debug(f"Executing sparse vector search query with sparse_vector: {sparse_vector_str}")
                    # Execute the query
                    results = conn.execute(stmt)
                    rows = results.fetchall()
            
            # Convert results to OutputData objects
            sparse_results = []
            for row in rows:
                parsed = self._parse_row_to_dict(row, include_vector=False, extract_score=True)
                
                # Convert negative_inner_product to similarity (0-1 range, higher is better)
                # negative_inner_product returns negative values, negate to get inner product
                sparse_score = parsed["score_or_distance"]
                if sparse_score is not None:
                    inner_prod = -float(sparse_score)
                    # Convert inner product to similarity using sigmoid-like transformation
                    # For positive inner products: similarity = inner_prod / (1 + inner_prod)
                    # This maps [0, inf) to [0, 1)
                    if inner_prod >= 0:
                        similarity = inner_prod / (1.0 + inner_prod)
                    else:
                        # For negative inner products, map to very low similarity
                        similarity = max(0.0, 1.0 / (1.0 - inner_prod))
                else:
                    similarity = 0.0
                
                # Store original similarity in metadata
                metadata = parsed["metadata"]
                metadata['_sparse_similarity'] = similarity
                
                sparse_results.append(self._create_output_data(
                    parsed["vector_id"], 
                    parsed["text_content"], 
                    similarity, 
                    metadata
                ))
            
            logger.debug(f"_sparse_search results, len : {len(sparse_results)}")
            return sparse_results
            
        except Exception as e:
            logger.error(f"Sparse vector search failed: {e}", exc_info=True)
            # Return empty results on error rather than raising
            return []

    def _hybrid_search(self, query: str, vectors: List[List[float]], limit: int = 5, filters: Optional[Dict] = None,
                       sparse_embedding: Optional[Dict[int, float]] = None,
                       fusion_method: str = "rrf", k: int = 60):
        """Perform hybrid search combining vector, full-text, and sparse vector search with optional reranking."""
        # Determine candidate limit for reranking
        candidate_limit = limit * 3 if self.reranker else limit

        # Determine which searches to perform
        perform_sparse = self.include_sparse and sparse_embedding is not None
        
        # Perform searches in parallel for better performance
        search_tasks = []
        with ThreadPoolExecutor(max_workers=3 if perform_sparse else 2) as executor:
            # Submit vector search
            vector_future = executor.submit(self._vector_search, query, vectors, candidate_limit, filters)
            search_tasks.append(('vector', vector_future))
            
            # Submit full-text search
            fts_future = executor.submit(self._fulltext_search, query, candidate_limit, filters)
            search_tasks.append(('fts', fts_future))
            
            # Submit sparse vector search if enabled
            if perform_sparse:
                sparse_future = executor.submit(self._sparse_search, sparse_embedding, candidate_limit, filters)
                search_tasks.append(('sparse', sparse_future))
            
            # Wait for all searches to complete and get results
            vector_results = None
            fts_results = None
            sparse_results = None
            
            for search_type, future in search_tasks:
                try:
                    results = future.result()
                    if search_type == 'vector':
                        vector_results = results
                    elif search_type == 'fts':
                        fts_results = results
                    elif search_type == 'sparse':
                        sparse_results = results
                except Exception as e:
                    logger.warning(f"{search_type} search failed: {e}")
                    if search_type == 'vector':
                        vector_results = []
                    elif search_type == 'fts':
                        fts_results = []
                    elif search_type == 'sparse':
                        sparse_results = []

        # Ensure we have at least empty lists
        if vector_results is None:
            vector_results = []
        if fts_results is None:
            fts_results = []
        if sparse_results is None:
            sparse_results = []

        # Step 1: Coarse ranking - Combine results using RRF or weighted fusion
        coarse_ranked_results = self._combine_search_results(
            vector_results, fts_results, sparse_results, candidate_limit, fusion_method, k, sparse_embedding
        )
        logger.debug(f"Coarse ranking completed, candidates: {len(coarse_ranked_results)}")
        
        # Step 2: Fine ranking - Use Rerank model for precision sorting (if enabled)
        if self.reranker and query and coarse_ranked_results:
            try:
                final_results = self._apply_rerank(query, coarse_ranked_results, limit)
                logger.debug(f"Rerank applied, final results: {len(final_results)}")
                return final_results
            except Exception as e:
                logger.warning(f"Rerank failed, falling back to coarse ranking: {e}")
                return coarse_ranked_results[:limit]
        else:
            # No reranker, return coarse ranking results
            return coarse_ranked_results[:limit]

    def _apply_rerank(self, query: str, candidates: List[OutputData], limit: int) -> List[OutputData]:
        """
        Apply Rerank model for precision sorting.
        
        Args:
            query: Search query text
            candidates: Candidate results from coarse ranking
            limit: Number of final results to return
            
        Returns:
            List of reranked OutputData objects
        """
        if not candidates:
            return []
        
        # Extract document texts from candidates
        documents = [result.payload.get('data', '') for result in candidates]

        # Call reranker to get reranked indices and scores
        reranked_indices = self.reranker.rerank(query, documents, top_n=limit)
        
        # Reconstruct results with rerank scores
        final_results = []
        for idx, rerank_score in reranked_indices:
            result = candidates[idx]
            # Preserve original scores in payload
            result.payload['_fusion_score'] = result.score
            # Update score to rerank score
            result.score = rerank_score
            result.payload['_rerank_score'] = rerank_score
            final_results.append(result)
        
        # Reorder results: high scores on both ends, low scores in the middle
        if len(final_results) > 1:
            reordered = [None] * len(final_results)
            left = 0
            right = len(final_results) - 1
            
            for i, result in enumerate(final_results):
                if i % 2 == 0:
                    # Even indices go to the left side
                    reordered[left] = result
                    left += 1
                else:
                    # Odd indices go to the right side
                    reordered[right] = result
                    right -= 1
            
            final_results = reordered
        
        logger.debug(f"Rerank completed: {len(final_results)} results")

        return final_results

    def _calculate_quality_score(
        self,
        vector_similarity: Optional[float] = None,
        fts_score: Optional[float] = None,
        sparse_similarity: Optional[float] = None,
        vector_weight: float = 0.5,
        fts_weight: float = 0.3,
        sparse_weight: float = 0.2
    ) -> float:
        """
        Calculate quality score from multiple search paths.
        
        Quality score represents the absolute similarity quality (0-1 range),
        used for threshold filtering. Unlike fusion scores used for ranking,
        quality scores maintain semantic meaning across different search scenarios.
        
        Args:
            vector_similarity: Vector search similarity (0-1, higher is better)
            fts_score: Full-text search score (0-1, higher is better)
            sparse_similarity: Sparse vector search similarity (0-1, higher is better)
            vector_weight: Weight for vector search (default: 0.5)
            fts_weight: Weight for full-text search (default: 0.3)
            sparse_weight: Weight for sparse vector search (default: 0.2)
            
        Returns:
            Quality score in range [0, 1], where higher means better quality
            
        Algorithm:
            1. Identify which search paths participated (have non-None scores)
            2. Sum the weights of active paths
            3. Calculate weighted average: sum(weight_i / total_weight * score_i)
            4. This ensures quality score is always in [0, 1] regardless of which paths participated
        """
        # Collect active search paths and their scores
        active_paths = []
        
        if vector_similarity is not None:
            active_paths.append((vector_weight, vector_similarity))
        
        if fts_score is not None:
            active_paths.append((fts_weight, fts_score))
        
        if sparse_similarity is not None:
            active_paths.append((sparse_weight, sparse_similarity))
        
        # If no active paths, return 0
        if not active_paths:
            return 0.0
        
        # Calculate total weight of active paths
        total_weight = sum(weight for weight, _ in active_paths)
        
        # Handle edge case where total weight is 0
        if total_weight == 0:
            return 0.0
        
        # Calculate weighted average quality score
        quality_score = sum(
            (weight / total_weight) * score 
            for weight, score in active_paths
        )
        
        # Ensure result is in [0, 1] range
        return max(0.0, min(1.0, quality_score))

    def _combine_search_results(self, vector_results: List[OutputData], fts_results: List[OutputData],
                                sparse_results: Optional[List[OutputData]],
                                limit: int, fusion_method: str = "rrf", k: int = 60, sparse_embedding: Optional[Dict[int, float]] = None):
        """Combine and rerank vector, full-text, and sparse vector search results using RRF or weighted fusion."""
        if sparse_results is None:
            sparse_results = []
        
        if fusion_method == "rrf":
            return self._rrf_fusion(vector_results, fts_results, sparse_results, limit, k, sparse_embedding)
        else:
            return self._weighted_fusion(vector_results, fts_results, sparse_results, limit)

    def _normalize_weights_adaptively(
        self,
        all_docs: Dict,
        vector_w: float,
        fts_w: float,
        sparse_w: float,
        k: int = 60
    ) -> Dict:
        """
        Adaptively normalize weights for each document.
        
        Principle: Dynamically adjust the total weight to 1.0 based on how many retrieval paths
        the document was actually retrieved from, solving the unfairness issue in mixed states
        (some data has sparse vectors, some don't).
        
        Args:
            all_docs: Document dictionary {doc_id: {'result': ..., 'vector_rank': ..., 'fts_rank': ..., 'sparse_rank': ..., 'rrf_score': ...}}
            vector_w: Vector search weight
            fts_w: Full-text search weight
            sparse_w: Sparse vector search weight
            k: RRF constant (default: 60)
        
        Returns:
            Normalized all_docs (rrf_score modified)
        """
        for doc_id, doc_data in all_docs.items():
            # Count how many retrieval paths this document was retrieved from and their corresponding weights
            active_weights = []
            if doc_data['vector_rank'] is not None:
                active_weights.append(('vector', vector_w, doc_data['vector_rank']))
            if doc_data['fts_rank'] is not None:
                active_weights.append(('fts', fts_w, doc_data['fts_rank']))
            if doc_data['sparse_rank'] is not None:
                active_weights.append(('sparse', sparse_w, doc_data['sparse_rank']))
            
            # Calculate total effective weight
            total_weight = sum(w for _, w, _ in active_weights)
            
            if total_weight == 0:
                continue
            
            # Normalize and recalculate rrf_score
            normalized_score = 0.0
            
            for path, weight, rank in active_weights:
                normalized_weight = weight / total_weight
                normalized_score += normalized_weight * (1.0 / (k + rank))
            
            doc_data['rrf_score'] = normalized_score
        
        return all_docs

    def _rrf_fusion(self, vector_results: List[OutputData], fts_results: List[OutputData],
                    sparse_results: Optional[List[OutputData]],
                    limit: int, k: int = 60, sparse_embedding: Optional[Dict[int, float]] = None):
        """
        Reciprocal Rank Fusion (RRF) for combining search results from vector, FTS, and sparse vector searches.
        
        Uses weights configured at initialization.If the sparse weight is not provided, it will be set to 0.
        """
        if sparse_results is None:
            sparse_results = []
        
        vector_w = self.vector_weight if self.vector_weight is not None else 0
        fts_w = self.fts_weight if self.fts_weight is not None else 0
        sparse_w = 0

        if self.include_sparse and sparse_results and sparse_embedding:
            sparse_w = self.sparse_weight if self.sparse_weight is not None else 0
        
        # Create mapping of document ID to result data
        all_docs = {}

        # Process vector search results (rank-based scoring with weight)
        for rank, result in enumerate(vector_results, 1):
            rrf_score = vector_w * (1.0 / (k + rank))
            all_docs[result.id] = {
                'result': result,
                'vector_rank': rank,
                'fts_rank': None,
                'sparse_rank': None,
                'rrf_score': rrf_score
            }

        # Process FTS results (add or update RRF scores with weight)
        for rank, result in enumerate(fts_results, 1):
            fts_rrf_score = fts_w * (1.0 / (k + rank))

            if result.id in all_docs:
                # Document found in previous searches - combine RRF scores
                all_docs[result.id]['fts_rank'] = rank
                all_docs[result.id]['rrf_score'] += fts_rrf_score
            else:
                # Document only in FTS results
                all_docs[result.id] = {
                    'result': result,
                    'vector_rank': None,
                    'fts_rank': rank,
                    'sparse_rank': None,
                    'rrf_score': fts_rrf_score
                }

        # Process sparse vector search results (add or update RRF scores with weight)
        for rank, result in enumerate(sparse_results, 1):
            sparse_rrf_score = sparse_w * (1.0 / (k + rank))

            if result.id in all_docs:
                # Document found in previous searches - combine RRF scores
                all_docs[result.id]['sparse_rank'] = rank
                all_docs[result.id]['rrf_score'] += sparse_rrf_score
            else:
                # Document only in sparse results
                all_docs[result.id] = {
                    'result': result,
                    'vector_rank': None,
                    'fts_rank': None,
                    'sparse_rank': rank,
                    'rrf_score': sparse_rrf_score
                }

        # Adaptive weight normalization: solve unfairness in mixed states
        # For each document, re-normalize weights based on the actual number of participating paths
        all_docs = self._normalize_weights_adaptively(
            all_docs, vector_w, fts_w, sparse_w, k
        )
        logger.debug("Applied adaptive weight normalization")

        # Convert to final results and sort by RRF score
        heap = []
        for doc_id, doc_data in all_docs.items():
            # Use document ID as tiebreaker to avoid dict comparison when rrf_scores are equal
            if len(heap) < limit:
                heapq.heappush(heap, (doc_data['rrf_score'], doc_id, doc_data))
            elif doc_data['rrf_score'] > heap[0][0]:
                heapq.heapreplace(heap, (doc_data['rrf_score'], doc_id, doc_data))

        final_results = []
        for score, _, doc_data in sorted(heap, key=lambda x: x[0], reverse=True):
            result = doc_data['result']
            
            # Extract original similarity scores from metadata
            vector_similarity = result.payload.get('_vector_similarity')
            fts_score = result.payload.get('_fts_score')
            sparse_similarity = result.payload.get('_sparse_similarity')
            
            # Calculate quality score for threshold filtering
            quality_score = self._calculate_quality_score(
                vector_similarity=vector_similarity,
                fts_score=fts_score,
                sparse_similarity=sparse_similarity,
                vector_weight=vector_w,
                fts_weight=fts_w,
                sparse_weight=sparse_w
            )
            
            # Store quality score in payload
            result.payload['_quality_score'] = quality_score
            
            # Store fusion score (RRF score) in payload for debugging
            result.payload['_fusion_score'] = score
            
            # Set result.score to fusion score (used for ranking)
            result.score = score
            # Add ranking information to metadata for debugging
            result.payload['_fusion_info'] = {
                'vector_rank': doc_data['vector_rank'],
                'fts_rank': doc_data['fts_rank'],
                'sparse_rank': doc_data['sparse_rank'],
                'rrf_score': score,
                'fusion_method': 'rrf',
                'vector_weight': vector_w,
                'fts_weight': fts_w,
                'sparse_weight': sparse_w
            }
            final_results.append(result)

        return final_results

    def _weighted_fusion(self, vector_results: List[OutputData], fts_results: List[OutputData],
                         sparse_results: Optional[List[OutputData]],
                         limit: int, vector_weight: float = 0.7, text_weight: float = 0.3, sparse_weight: float = 0.0):
        """
        Traditional weighted score fusion (fallback method).
        
        Note: All input scores are already in 0-1 similarity range (higher is better),
        so no normalization is needed.
        """
        if sparse_results is None:
            sparse_results = []
        
        # Use instance weights if available
        vector_w = self.vector_weight if self.vector_weight is not None else vector_weight
        fts_w = self.fts_weight if self.fts_weight is not None else text_weight
        sparse_w = 0.0
        if self.include_sparse and sparse_results:
            sparse_w = self.sparse_weight if self.sparse_weight is not None else sparse_weight
        
        # Create a mapping of id to results for deduplication
        combined_results = {}

        # Process vector search results (scores are already 0-1 similarity)
        for result in vector_results:
            combined_results[result.id] = {
                'result': result,
                'vector_score': result.score,  # Already 0-1 similarity
                'fts_score': 0.0,
                'sparse_score': 0.0
            }

        # Add FTS results (scores are already 0-1)
        for result in fts_results:
            if result.id in combined_results:
                # Update existing result with FTS score
                combined_results[result.id]['fts_score'] = result.score
            else:
                # Add new FTS-only result
                combined_results[result.id] = {
                    'result': result,
                    'vector_score': 0.0,
                    'fts_score': result.score,
                    'sparse_score': 0.0
                }
        
        # Add sparse vector search results (scores are already 0-1 similarity)
        for result in sparse_results:
            if result.id in combined_results:
                # Update existing result with sparse score
                combined_results[result.id]['sparse_score'] = result.score
            else:
                # Add new sparse-only result
                combined_results[result.id] = {
                    'result': result,
                    'vector_score': 0.0,
                    'fts_score': 0.0,
                    'sparse_score': result.score
                }

        # Calculate combined scores and create final results
        heap = []
        for doc_id, doc_data in combined_results.items():
            combined_score = (vector_w * doc_data['vector_score'] +
                              fts_w * doc_data['fts_score'] +
                              sparse_w * doc_data['sparse_score'])

            if len(heap) < limit:
                heapq.heappush(heap, (combined_score, doc_id, doc_data))
            elif combined_score > heap[0][0]:
                heapq.heapreplace(heap, (combined_score, doc_id, doc_data))

        final_results = []
        for score, _, doc_data in sorted(heap, key=lambda x: x[0], reverse=True):
            result = doc_data['result']
            
            # Extract original similarity scores from metadata
            vector_similarity = result.payload.get('_vector_similarity')
            fts_score = result.payload.get('_fts_score')
            sparse_similarity = result.payload.get('_sparse_similarity')
            
            # Calculate quality score for threshold filtering
            quality_score = self._calculate_quality_score(
                vector_similarity=vector_similarity,
                fts_score=fts_score,
                sparse_similarity=sparse_similarity,
                vector_weight=vector_w,
                fts_weight=fts_w,
                sparse_weight=sparse_w
            )
            
            # Store quality score in payload
            result.payload['_quality_score'] = quality_score
            
            # Store fusion score in payload for debugging
            result.payload['_fusion_score'] = score
            
            # Set result.score to fusion score (used for ranking)
            result.score = score
            # Add fusion info for debugging
            result.payload['_fusion_info'] = {
                'vector_score': doc_data['vector_score'],
                'fts_score': doc_data['fts_score'],
                'sparse_score': doc_data['sparse_score'],
                'combined_score': score,
                'fusion_method': 'weighted',
                'vector_weight': vector_w,
                'fts_weight': fts_w,
                'sparse_weight': sparse_w
            }
            final_results.append(result)

        # Return top results
        return final_results

    def delete(self, vector_id: int):
        """Delete a vector by ID."""
        try:
            self.obvector.delete(
                table_name=self.collection_name,
                ids=[vector_id],
            )
            logger.debug(f"Successfully deleted vector with ID: {vector_id} from collection '{self.collection_name}'")
        except Exception as e:
            logger.error(f"Failed to delete vector with ID {vector_id} from collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def update(self, vector_id: int, vector: Optional[List[float]] = None, payload: Optional[Dict] = None):
        """Update a vector and its payload."""
        try:
            # Get existing record to preserve fields not being updated
            # Always try to get sparse_vector_field to preserve it even when include_sparse=False
            # This prevents accidentally clearing sparse_embedding when using a non-sparse Memory instance
            output_columns = [self.vector_field]
            has_sparse_column = OceanBaseUtil.check_column_exists(self.obvector, self.collection_name, self.sparse_vector_field)
            if has_sparse_column:
                output_columns.append(self.sparse_vector_field)
            
            existing_result = self.obvector.get(
                table_name=self.collection_name,
                ids=[vector_id],
                output_column_name=output_columns
            )

            existing_rows = existing_result.fetchall()
            if not existing_rows:
                logger.warning(f"Vector with ID {vector_id} not found in collection '{self.collection_name}'")
                return

            # Prepare update data
            update_data: Dict[str, Any] = {
                self.primary_field: vector_id,
            }

            # Extract existing values from row
            existing_vector = existing_rows[0][0] if existing_rows[0] else None
            existing_sparse_embedding = existing_rows[0][1] if has_sparse_column and len(existing_rows[0]) > 1 else None

            if vector is not None:
                update_data[self.vector_field] = (
                    vector if not self.normalize else OceanBaseUtil.normalize(vector)
                )
            else:
                # Preserve the existing vector to avoid it being cleared by upsert
                if existing_vector is not None:
                    update_data[self.vector_field] = existing_vector
                    logger.debug(f"Preserving existing vector for ID {vector_id}")

            if payload is not None:
                # Use the helper method to build fields, then merge with update_data
                temp_record = self._build_record_for_insert(vector or [], payload)

                # Copy relevant fields from temp_record (excluding primary key and vector if not updating)
                for key, value in temp_record.items():
                    if key != self.primary_field and (vector is not None or key != self.vector_field):
                        update_data[key] = value

            # Preserve existing sparse_embedding if not explicitly provided in payload
            # This prevents intelligence_plugin updates from accidentally clearing sparse_embedding
            # Check column existence instead of include_sparse to protect data even when sparse is disabled
            if has_sparse_column and self.sparse_vector_field not in update_data:
                if existing_sparse_embedding is not None:
                    update_data[self.sparse_vector_field] = existing_sparse_embedding
                    logger.debug(f"Preserving existing sparse_embedding for ID {vector_id}")

            # Update record
            self.obvector.upsert(
                table_name=self.collection_name,
                data=[update_data],
            )
            logger.debug(f"Successfully updated vector with ID: {vector_id} in collection '{self.collection_name}'")
            
        except Exception as e:
            logger.error(f"Failed to update vector with ID {vector_id} in collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def get(self, vector_id: int):
        """Retrieve a vector by ID."""
        try:
            # Build output column name list
            output_columns = self._get_standard_column_names(include_vector_field=True)
            
            results = self.obvector.get(
                table_name=self.collection_name,
                ids=[vector_id],
                output_column_name=output_columns,
            )

            rows = results.fetchall()
            if not rows:
                logger.debug(f"Vector with ID {vector_id} not found in collection '{self.collection_name}'")
                return None

            parsed = self._parse_row_to_dict(rows[0], include_vector=True, extract_score=False)

            logger.debug(f"Successfully retrieved vector with ID: {vector_id} from collection '{self.collection_name}'")
            return self._create_output_data(
                parsed["vector_id"], 
                parsed["text_content"], 
                0.0, 
                parsed["metadata"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get vector with ID {vector_id} from collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def list_cols(self):
        """List all collections."""
        try:
            # Get all tables from the database using the correct SQLAlchemy API
            with self.obvector.engine.connect() as conn:
                result = conn.execute(text("SHOW TABLES"))
                tables = [row[0] for row in result.fetchall()]
                logger.debug(f"Successfully listed {len(tables)} collections")
                return tables
        except Exception as e:
            logger.error(f"Failed to list collections: {e}", exc_info=True)
            raise

    def delete_col(self):
        """Delete the collection."""
        try:
            if self.obvector.check_table_exists(self.collection_name):
                self.obvector.drop_table_if_exist(self.collection_name)
                logger.info(f"Successfully deleted collection '{self.collection_name}'")
            else:
                logger.warning(f"Collection '{self.collection_name}' does not exist, skipping deletion")
        except Exception as e:
            logger.error(f"Failed to delete collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def _get_existing_vector_dimension(self) -> Optional[int]:
        """Get the dimension of the existing vector field in the table."""
        if not self.obvector.check_table_exists(self.collection_name):
            return None

        try:
            # Get table schema information using the correct SQLAlchemy API
            with self.obvector.engine.connect() as conn:
                result = conn.execute(text(f"DESCRIBE {self.collection_name}"))
                columns = result.fetchall()

            # Find the vector field and extract its dimension
            for col in columns:
                if col[0] == self.vector_field:
                    # Parse vector type like "VECTOR(1536)" to extract dimension
                    col_type = col[1]
                    if col_type.startswith("VECTOR(") and col_type.endswith(")"):
                        dim_str = col_type[7:-1]  # Extract dimension from "VECTOR(1536)"
                        return int(dim_str)
            return None
        except Exception as e:
            logger.warning(f"Failed to get vector dimension for table {self.collection_name}: {e}")
            return None

    def col_info(self):
        """Get information about the collection."""
        try:
            if not self.obvector.check_table_exists(self.collection_name):
                logger.debug(f"Collection '{self.collection_name}' does not exist")
                return None

            # Get table schema information using the correct SQLAlchemy API
            with self.obvector.engine.connect() as conn:
                result = conn.execute(text(f"DESCRIBE {self.collection_name}"))
                columns = result.fetchall()

            logger.debug(f"Successfully retrieved info for collection '{self.collection_name}'")
            return {
                "name": self.collection_name,
                "columns": [{"name": col[0], "type": col[1]} for col in columns],
                "index_type": self.index_type,
                "metric_type": self.vidx_metric_type,
            }
            
        except Exception as e:
            logger.error(f"Failed to get collection info for '{self.collection_name}': {e}", exc_info=True)
            raise

    def list(self, filters: Optional[Dict] = None, limit: Optional[int] = None):
        """List all memories."""
        try:
            table = Table(self.collection_name, self.obvector.metadata_obj, autoload_with=self.obvector.engine)
            
            # Build where clause from filters using the same table object
            where_clause = self._generate_where_clause(filters, table=table)

            # Build output column name list
            output_columns = self._get_standard_column_names(include_vector_field=True)

            # Get all records
            results = self.obvector.get(
                table_name=self.collection_name,
                ids=None,
                output_column_name=output_columns,
                where_clause=where_clause
            )

            memories = []
            for row in results.fetchall():
                parsed = self._parse_row_to_dict(row, include_vector=True, extract_score=False)

                memories.append(self._create_output_data(
                    parsed["vector_id"], 
                    parsed["text_content"], 
                    0.0, 
                    parsed["metadata"]
                ))

            if limit:
                memories = memories[:limit]

            logger.debug(f"Successfully listed {len(memories)} memories from collection '{self.collection_name}'")
            return [memories]
            
        except Exception as e:
            logger.error(f"Failed to list memories from collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def reset(self):
        """
        Reset collection by deleting and recreating it.
        
        Note: After reset, the table will be recreated with the current configuration.
        If include_sparse=True and database supports it, sparse vector will be included.
        For existing tables that need sparse vector support, use the upgrade script:
        
            from script import ScriptManager
            from powermem import auto_config
            config = auto_config()
            ScriptManager.run('upgrade-sparse-vector', config)
        """
        try:
            logger.info(f"Resetting collection '{self.collection_name}'")
            self.delete_col()
            
            if self.embedding_model_dims is not None:
                # Create baseline table (020: dense vector + fulltext only)
                self._create_table_with_index_by_embedding_model_dims()

            if self.hybrid_search:
                self._check_and_create_fulltext_index()
            
            # Note: Sparse vector support is NOT created in reset()
            # Users should reinitialize OceanBaseVectorStore to get upgraded features
            
            logger.info(
                f"Successfully reset collection '{self.collection_name}' to baseline schema (020). "
            )
            
        except Exception as e:
            logger.error(f"Failed to reset collection '{self.collection_name}': {e}", exc_info=True)
            raise

    def _check_and_create_fulltext_index(self):
        # Check whether the full-text index exists, if not, create it
        if not OceanBaseUtil.check_fulltext_index_exists(
            self.obvector, self.collection_name, self.fulltext_field
        ):
            self._create_fulltext_index()

    def _create_fulltext_index(self):
        try:
            logger.debug(
                "About to create fulltext index for collection '%s' using parser '%s'",
                self.collection_name,
                self.fulltext_parser,
            )

            # Create fulltext index with the specified parser using SQL
            with self.obvector.engine.connect() as conn:
                sql_command = text(f"""ALTER TABLE {self.collection_name}
                    ADD FULLTEXT INDEX fulltext_index_for_col_text ({self.fulltext_field}) WITH PARSER {self.fulltext_parser}""")

                logger.debug("DEBUG: Executing SQL: %s", sql_command)
                conn.execute(sql_command)
                logger.debug("DEBUG: Fulltext index created successfully for '%s'", self.collection_name)

        except Exception as e:
            logger.exception("Exception occurred while creating fulltext index")
            raise Exception(
                "Failed to add fulltext index to the target table, your OceanBase version must be "
                "4.3.5.1 or above to support fulltext index and vector index in the same table"
            ) from e

        # Refresh metadata
        self.obvector.refresh_metadata([self.collection_name])
    
    def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL statement and return results.
        
        This method is used by SubStoreMigrationManager to manage migration status table.
        
        Args:
            sql: SQL statement to execute
            params: Optional parameters for the SQL statement
            
        Returns:
            List of result rows as dictionaries
        """
        try:
            with self.obvector.engine.connect() as conn:
                if params:
                    result = conn.execute(text(sql), params)
                else:
                    result = conn.execute(text(sql))
                
                # Commit for DDL/DML statements
                conn.commit()
                
                # Try to fetch results (for SELECT queries)
                try:
                    rows = result.fetchall()
                    # Convert rows to dictionaries
                    if rows and result.keys():
                        return [dict(zip(result.keys(), row)) for row in rows]
                    return []
                except Exception:
                    # No results to fetch (for INSERT/UPDATE/DELETE/CREATE)
                    return []
                    
        except Exception as e:
            logger.error(f"Failed to execute SQL: {e}")
            logger.debug(f"SQL statement: {sql}")
            raise