"""
OceanBase utility functions

This module provides utility functions for checking OceanBase database information.
"""
import json
import logging
import re
from typing import Dict, Optional, List

try:
    from sqlalchemy import text
    from sqlalchemy.schema import CreateTable
    from pyobvector import FtsParser
    from pyobvector.schema import ObTable, VectorIndex, FtsIndex
    from pyobvector.client.index_param import IndexParams
    from pyobvector.client.fts_index_param import FtsIndexParam
    from pyobvector.client.partitions import ObPartition
except ImportError as e:
    raise ImportError(
        f"Required dependencies not found: {e}. Please install sqlalchemy and pyobvector."
    )

logger = logging.getLogger(__name__)


class OceanBaseUtil:
    """Utility class for OceanBase database checks and information retrieval."""

    @staticmethod
    def check_table_exists(obvector, table_name: str) -> bool:
        """
        Check if a table exists.

        Args:
            obvector: The ObVecClient instance.
            table_name: The name of the table.

        Returns:
            True if the table exists, False otherwise.
        """
        try:
            with obvector.engine.connect() as conn:
                result = conn.execute(text(
                    f"SELECT COUNT(*) FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA = DATABASE() "
                    f"AND TABLE_NAME = '{table_name}'"
                ))
                return result.scalar() > 0
        except Exception as e:
            logger.error(f"An error occurred while checking if table exists: {e}")
            return False

    @staticmethod
    def check_column_exists(obvector, table_name: str, column_name: str) -> bool:
        """
        Check if a column exists in a table.

        Args:
            obvector: The ObVecClient instance.
            table_name: The name of the table.
            column_name: The name of the column.

        Returns:
            True if the column exists, False otherwise.
        """
        try:
            with obvector.engine.connect() as conn:
                result = conn.execute(text(
                    f"SELECT COUNT(*) FROM information_schema.COLUMNS "
                    f"WHERE TABLE_SCHEMA = DATABASE() "
                    f"AND TABLE_NAME = '{table_name}' "
                    f"AND COLUMN_NAME = '{column_name}'"
                ))
                return result.scalar() > 0
        except Exception as e:
            logger.error(f"An error occurred while checking if column exists: {e}")
            return False

    @staticmethod
    def check_sparse_vector_column_exists(
        obvector, collection_name: str, sparse_vector_field: str
    ) -> bool:
        """
        Check if the sparse vector column exists.

        Args:
            obvector: The ObVecClient instance.
            collection_name: The name of the collection/table.
            sparse_vector_field: The name of the sparse vector field.

        Returns:
            True if the sparse vector column exists, False otherwise.
        """
        # Use the generic check_column_exists method
        return OceanBaseUtil.check_column_exists(obvector, collection_name, sparse_vector_field)

    @staticmethod
    def check_index_exists(obvector, table_name: str, index_name: str) -> bool:
        """
        Check if an index exists on a table (generic index check method).

        Args:
            obvector: The ObVecClient instance.
            table_name: The name of the table.
            index_name: The name of the index.

        Returns:
            True if the index exists, False otherwise.
        """
        try:
            with obvector.engine.connect() as conn:
                result = conn.execute(text(
                    f"SELECT COUNT(*) FROM information_schema.STATISTICS "
                    f"WHERE TABLE_SCHEMA = DATABASE() "
                    f"AND TABLE_NAME = '{table_name}' "
                    f"AND INDEX_NAME = '{index_name}'"
                ))
                return result.scalar() > 0
        except Exception as e:
            logger.error(f"An error occurred while checking if index exists: {e}")
            return False

    @staticmethod
    def check_sparse_vector_index_exists(obvector, collection_name: str) -> bool:
        """
        Check if the sparse vector index exists.

        Args:
            obvector: The ObVecClient instance.
            collection_name: The name of the collection/table.

        Returns:
            True if the sparse vector index exists, False otherwise.
        """
        # Use the generic check_index_exists method
        return OceanBaseUtil.check_index_exists(obvector, collection_name, "sparse_embedding_idx")

    @staticmethod
    def check_fulltext_index_exists(obvector, collection_name: str, fulltext_field: str) -> bool:
        """
        Check if the full-text index of the specified table exists.

        Args:
            obvector: The ObVecClient instance.
            collection_name: The name of the collection/table.
            fulltext_field: The name of the fulltext field.

        Returns:
            True if the full-text index exists, False otherwise.
        """
        try:
            with obvector.engine.connect() as conn:
                result = conn.execute(text(f"SHOW INDEX FROM {collection_name}"))
                indexes = result.fetchall()

                for index in indexes:
                    # Index [2] is the index name, index [4] is the column name, and index [10] is the index type
                    if len(index) > 10 and index[10] == 'FULLTEXT':
                        if fulltext_field in str(index[4]):
                            return True

                return False

        except Exception as e:
            logger.error(f"An error occurred while checking the full-text index: {e}")
            return False

    @staticmethod
    def is_seekdb(obvector) -> bool:
        """
        Check if the database is seekdb.

        Args:
            obvector: The ObVecClient instance.

        Returns:
            True if database is seekdb, False otherwise.
        """
        try:
            with obvector.engine.connect() as conn:
                # Try to get version information
                # OceanBase uses SELECT VERSION() or SHOW VARIABLES LIKE 'version'
                try:
                    result = conn.execute(text("SELECT VERSION()"))
                    version_str = result.fetchone()[0]
                except Exception:
                    # Fallback to SHOW VARIABLES
                    try:
                        result = conn.execute(text("SHOW VARIABLES LIKE 'version'"))
                        row = result.fetchone()
                        version_str = row[1] if row else ""
                    except Exception:
                        return False

                if not version_str:
                    return False

                version_str = str(version_str).strip().lower()
                return "seekdb" in version_str

        except Exception as e:
            logger.warning(f"Error checking if database is seekdb: {e}")
            return False

    @staticmethod
    def get_version_number(obvector) -> Optional[Dict[str, int]]:
        """
        Get the database version number.

        Args:
            obvector: The ObVecClient instance.

        Returns:
            Dictionary with keys "major", "minor", "patch" and int values, e.g., {"major": 4, "minor": 5, "patch": 0}.
            Returns None if version cannot be determined.
        """
        try:
            with obvector.engine.connect() as conn:
                # Try to get version information
                # OceanBase uses SELECT VERSION() or SHOW VARIABLES LIKE 'version'
                try:
                    result = conn.execute(text("SELECT VERSION()"))
                    version_str = result.fetchone()[0]
                except Exception:
                    # Fallback to SHOW VARIABLES
                    try:
                        result = conn.execute(text("SHOW VARIABLES LIKE 'version'"))
                        row = result.fetchone()
                        version_str = row[1] if row else ""
                    except Exception:
                        return None

                if not version_str:
                    return None

                version_str = str(version_str).strip()
                # Parse version string
                # For OceanBase, prioritize the actual OceanBase version (e.g., "5.7.25-OceanBase_CE-v4.3.5.5" -> 4.3.5)
                # First try to match OceanBase version pattern
                version_match = re.search(r'OceanBase[^v]*[vV]?(\d+)\.(\d+)\.(\d+)', version_str)
                if not version_match:
                    version_match = re.search(r'[vV]?(\d+)\.(\d+)\.(\d+)', version_str)
                
                if version_match:
                    major = int(version_match.group(1))
                    minor = int(version_match.group(2))
                    patch = int(version_match.group(3))
                    return {"major": major, "minor": minor, "patch": patch}
                else:
                    return None

        except Exception as e:
            logger.warning(f"Error getting database version number: {e}")
            return None

    @staticmethod
    def check_sparse_vector_version_support(obvector) -> bool:
        """
        Check if the database version supports sparse vector.

        Args:
            obvector: The ObVecClient instance.

        Returns:
            True if version is seekdb or OceanBase >= 4.5.0, False otherwise.
        """
        # Check if it's seekdb
        if OceanBaseUtil.is_seekdb(obvector):
            logger.info("Detected seekdb, sparse vector is supported")
            return True

        # Check if it's OceanBase and version >= 4.5.0
        version_dict = OceanBaseUtil.get_version_number(obvector)
        if version_dict is None:
            logger.warning("Could not determine database version, assuming sparse vector not supported")
            return False

        major = version_dict["major"]
        minor = version_dict["minor"]
        patch = version_dict["patch"]

        if major > 4 or (major == 4 and minor >= 5):
            logger.info(f"Detected OceanBase version {major}.{minor}.{patch}, sparse vector is supported")
            return True
        else:
            logger.warning(
                f"Detected OceanBase version {major}.{minor}.{patch}, "
                "sparse vector requires OceanBase >= 4.5.0"
            )
            return False

    @staticmethod
    def check_native_hybrid_version_support(obvector, table_name: str) -> bool:
        """
        Check if the database version and table type support native hybrid search (DBMS_HYBRID_SEARCH.SEARCH).

        Args:
            obvector: The ObVecClient instance.
            table_name: The name of the table.

        Returns:
            True if version is seekdb or OceanBase >= 4.4.1, and table is heap table or doesn't exist.
            False otherwise.
        """
        # Check if it's seekdb
        if OceanBaseUtil.is_seekdb(obvector):
            logger.info("Detected seekdb, native hybrid search is supported")
            # Also check if table is heap table (or doesn't exist)
            if not OceanBaseUtil.check_table_is_heap_or_not_exists(obvector, table_name):
                logger.warning(
                    f"Table '{table_name}' is not a heap table (ORGANIZATION HEAP). "
                    "Native hybrid search requires heap table."
                )
                return False
            return True

        # Check if it's OceanBase and version >= 4.4.1
        version_dict = OceanBaseUtil.get_version_number(obvector)
        if version_dict is None:
            logger.warning("Could not determine database version, assuming native hybrid search not supported")
            return False

        major = version_dict["major"]
        minor = version_dict["minor"]
        patch = version_dict["patch"]

        # Check version >= 4.4.1
        if major > 4 or (major == 4 and (minor > 4 or (minor == 4 and patch >= 1))):
            logger.info(f"Detected OceanBase version {major}.{minor}.{patch}, native hybrid search is supported")
            # Also check if table is heap table (or doesn't exist)
            if not OceanBaseUtil.check_table_is_heap_or_not_exists(obvector, table_name):
                logger.warning(
                    f"Table '{table_name}' is not a heap table (ORGANIZATION HEAP). "
                    "Native hybrid search requires heap table."
                )
                return False
            return True
        else:
            logger.warning(
                f"Detected OceanBase version {major}.{minor}.{patch}, "
                "native hybrid search requires OceanBase >= 4.4.1"
            )
            return False

    @staticmethod
    def check_table_is_heap_or_not_exists(obvector, table_name: str) -> bool:
        """
        Check if the table is a heap table (ORGANIZATION HEAP) or doesn't exist.

        DBMS_HYBRID_SEARCH.SEARCH requires heap table, not index-organized table.

        Args:
            obvector: The ObVecClient instance.
            table_name: The name of the table.

        Returns:
            True if table is heap table or doesn't exist, False if it's index-organized table.
        """
        try:
            with obvector.engine.connect() as conn:
                # Check if table exists first
                result = conn.execute(text(
                    f"SELECT COUNT(*) FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA = DATABASE() "
                    f"AND TABLE_NAME = '{table_name}'"
                ))
                if result.scalar() == 0:
                    # Table doesn't exist, will be created as heap table
                    logger.debug(f"Table '{table_name}' doesn't exist, will be created as heap table")
                    return True

                # Check table organization type using SHOW CREATE TABLE
                result = conn.execute(text(f"SHOW CREATE TABLE `{table_name}`"))
                row = result.fetchone()
                if row and len(row) >= 2:
                    create_statement = row[1]
                    # Check for ORGANIZATION keyword
                    if "ORGANIZATION INDEX" in create_statement.upper():
                        logger.debug(f"Table '{table_name}' is an index-organized table")
                        return False
                    elif "ORGANIZATION HEAP" in create_statement.upper():
                        logger.debug(f"Table '{table_name}' is a heap table")
                        return True
                    else:
                        # No ORGANIZATION keyword, default is index-organized in OceanBase
                        # when table has primary key
                        logger.debug(f"Table '{table_name}' has no explicit ORGANIZATION, assuming index-organized")
                        return False
                return False
        except Exception as e:
            logger.error(f"An error occurred while checking table organization type: {e}")
            return False

    @staticmethod
    def format_sparse_vector(sparse_dict: Dict[int, float]) -> str:
        """
        Format sparse vector dictionary to string format for SQL query.
        
        Args:
            sparse_dict: Dictionary with token_id as key and weight as value, e.g., {3: 0.3, 4: 0.4}
            
        Returns:
            Formatted string like '{3:0.3, 4:0.4}'
        """
        if not sparse_dict:
            return "{}"
        formatted = "{" + ", ".join(f"{k}:{v}" for k, v in sparse_dict.items()) + "}"
        return formatted

    @staticmethod
    def normalize(vector: List[float]) -> List[float]:
        """Normalize vector using L2 normalization."""
        import numpy as np
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        arr = arr / norm
        return arr.tolist()

    @staticmethod
    def get_fts_parser_enum(parser_name: str):
        """Convert parser name string to FtsParser enum.
        
        Args:
            parser_name: Parser name string (e.g., 'ik', 'ngram', 'ngram2', 'beng', 'space')
            
        Returns:
            FtsParser enum value
            
        Raises:
            ValueError: If parser name is not supported
        """
        parser_mapping = {
            'ik': FtsParser.IK,
            'ngram': FtsParser.NGRAM,
            'ngram2': FtsParser.NGRAM2,
            'beng': FtsParser.BASIC_ENGLISH,
            'space': None,
            'jieba': FtsParser.JIEBA,
        }
        
        parser_lower = parser_name.lower()
        if parser_lower not in parser_mapping:
            supported = ', '.join(parser_mapping.keys())
            raise ValueError(
                f"Unsupported fulltext parser: {parser_name}. "
                f"Supported parsers are: {supported}"
            )
        
        return parser_mapping[parser_lower]

    @staticmethod
    def parse_metadata(metadata_json):
        """
        Parse metadata from OceanBase.

        SQLAlchemy's JSON type automatically deserializes to dict, but this method
        handles backward compatibility with legacy string-serialized data.
        """
        if isinstance(metadata_json, dict):
            # SQLAlchemy JSON type returns dict directly (preferred path)
            return metadata_json
        elif isinstance(metadata_json, str):
            # Legacy compatibility: handle manually serialized strings
            try:
                # First attempt to parse
                metadata = json.loads(metadata_json)
                # Check if it's still a string (double encoded - legacy bug)
                if isinstance(metadata, str):
                    try:
                        # Second attempt to parse
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {}
                return metadata
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    @staticmethod
    def check_sparse_vector_ready(obvector, collection_name: str, sparse_vector_field: str) -> bool:
        """
        Check if sparse vector support is ready (without creating anything).
        
        This method checks if sparse vector features are available:
        1. Database version supports sparse vector
        2. sparse_embedding column exists
        3. sparse_embedding_idx exists
        
        If any check fails, a warning is logged and False is returned.
        
        Args:
            obvector: The ObVecClient instance.
            collection_name: The name of the collection/table.
            sparse_vector_field: The name of the sparse vector field.
        
        Returns:
            bool: True if sparse vector is fully supported, False otherwise.
        """
        # Check if database version supports sparse vector
        if not OceanBaseUtil.check_sparse_vector_version_support(obvector):
            logger.warning(
                "Sparse vector support disabled: Database version does not support sparse vector. "
                "Sparse vector requires seekdb or OceanBase >= 4.5.0. "
                "Please upgrade your database to enable sparse vector support."
            )
            return False

        # Check if sparse_embedding column exists
        if not OceanBaseUtil.check_sparse_vector_column_exists(obvector, collection_name, sparse_vector_field):
            logger.warning(
                f"Sparse vector support disabled: Table '{collection_name}' does not have sparse_embedding column. "
                f"Please run the upgrade script to enable sparse vector support:\n"
                f"  from powermem import auto_config\n"
                f"  from script import ScriptManager\n"
                f"  config = auto_config()\n"
                f"  ScriptManager.run('upgrade-sparse-vector', config)"
            )
            return False

        # Check if sparse_embedding_idx index exists
        if not OceanBaseUtil.check_sparse_vector_index_exists(obvector, collection_name):
            logger.warning(
                f"Sparse vector support disabled: Table '{collection_name}' does not have sparse_embedding_idx index. "
                f"Please run the upgrade script to enable sparse vector support:\n"
                f"  from powermem import auto_config\n"
                f"  from script import ScriptManager\n"
                f"  config = auto_config()\n"
                f"  ScriptManager.run('upgrade-sparse-vector', config)"
            )
            return False
        
        logger.info(f"Sparse vector support validated successfully for table '{collection_name}'")
        return True

    @staticmethod
    def check_filters_all_in_columns(filters: Optional[Dict], model_class) -> bool:
        """
        Check if all filter fields are in standard table columns.

        This is used to determine if native hybrid search can be used.
        Native hybrid search doesn't support JSON path filtering well,
        so we only use it when all filters are on actual table columns.

        Args:
            filters: The filter conditions in mem0 format.
            model_class: SQLAlchemy ORM model class with __table__ attribute.

        Returns:
            True if all filter fields are in table columns, False otherwise.
        """
        if not filters:
            return True

        # Get column names from model_class
        try:
            table_columns = set(model_class.__table__.c.keys())
        except AttributeError:
            logger.warning("model_class does not have __table__ attribute, native hybrid search disabled")
            return False

        def check_filter_keys(filter_dict: Dict) -> bool:
            """Recursively check if all keys are in table columns."""
            for key, value in filter_dict.items():
                # Handle AND/OR logic
                if key in ["AND", "OR"]:
                    if isinstance(value, list):
                        for sub_filter in value:
                            if not check_filter_keys(sub_filter):
                                return False
                else:
                    # Check if this key is a table column
                    if key not in table_columns:
                        logger.debug(f"Filter field '{key}' not in table columns, native hybrid search disabled")
                        return False
            return True

        return check_filter_keys(filters)

    @staticmethod
    def convert_filters_to_native_format(
        filters: Optional[Dict],
        model_class,
        metadata_field: str = "metadata"
    ) -> List[Dict]:
        """
        Convert filter format to OceanBase native DBMS_HYBRID_SEARCH.SEARCH filter format.

        Follows the SEARCH API specification:
        - term: Exact match (strings, numbers, booleans)
        - range: Range queries (gte, gt, lte, lt)
        - match: Fuzzy match (full-text)
        - bool: Complex logic (must, should, must_not, filter)

        Args:
            filters: The filter conditions in mem0 format.
            model_class: SQLAlchemy ORM model class with __table__ attribute.
            metadata_field: Name of the metadata field (default: "metadata").

        Returns:
            List[Dict]: Filter conditions in OceanBase SEARCH native format.
        """
        if not filters:
            return []

        # Get column names from model_class
        try:
            table_columns = set(model_class.__table__.c.keys())
        except AttributeError:
            logger.warning("model_class does not have __table__ attribute")
            table_columns = set()

        def get_field_name(key: str) -> Optional[str]:
            """Get the field name for filter."""
            if key in table_columns:
                return key
            else:
                # Skip non-table fields for native hybrid search
                logger.debug(f"Skipping non-table field in native hybrid search: {key}")
                return None

        def process_single_filter(key: str, value) -> Optional[Dict]:
            """Process a single filter condition."""
            field_name = get_field_name(key)
            if field_name is None:
                return None

            # List values -> IN query -> bool.should with multiple term queries
            if isinstance(value, list):
                if not value:
                    return None
                return {
                    "bool": {
                        "should": [{"term": {field_name: v}} for v in value]
                    }
                }

            # Dict values -> May be range query or other operators
            if isinstance(value, dict):
                range_ops = {"gte", "gt", "lte", "lt"}
                if any(op in value for op in range_ops):
                    range_params = {op: value[op] for op in range_ops if op in value}
                    return {"range": {field_name: range_params}}

                if "eq" in value:
                    return {"term": {field_name: value["eq"]}}

                if "ne" in value:
                    return {"bool": {"must_not": [{"term": {field_name: value["ne"]}}]}}

                if "in" in value:
                    if not isinstance(value["in"], list) or not value["in"]:
                        return None
                    return {
                        "bool": {
                            "should": [{"term": {field_name: v}} for v in value["in"]]
                        }
                    }

                if "nin" in value:
                    if not isinstance(value["nin"], list) or not value["nin"]:
                        return None
                    return {
                        "bool": {
                            "must_not": [
                                {"bool": {"should": [{"term": {field_name: v}} for v in value["nin"]]}}
                            ]
                        }
                    }

                if "like" in value or "ilike" in value:
                    query_str = value.get("like") or value.get("ilike")
                    query_str = str(query_str).replace("%", "").replace("_", " ").strip()
                    if query_str:
                        return {"match": {field_name: {"query": query_str}}}
                    return None

            # None values -> Not supported, skip
            if value is None:
                logger.warning(f"NULL filter not supported in native search, skipping: {key}")
                return None

            # Simple values -> term query
            return {"term": {field_name: value}}

        def process_complex_filter(filters_dict: Dict) -> List[Dict]:
            """Process complex filters with AND/OR logic."""
            if "AND" in filters_dict:
                conditions = []
                for sub_filter in filters_dict["AND"]:
                    result = process_complex_filter(sub_filter)
                    if result:
                        conditions.extend(result)
                if conditions:
                    return [{"bool": {"filter": conditions}}]
                return []

            if "OR" in filters_dict:
                conditions = []
                for sub_filter in filters_dict["OR"]:
                    result = process_complex_filter(sub_filter)
                    if result:
                        conditions.extend(result)
                if conditions:
                    return [{"bool": {"should": conditions}}]
                return []

            results = []
            for k, v in filters_dict.items():
                if k not in ["AND", "OR"]:
                    result = process_single_filter(k, v)
                    if result:
                        results.append(result)
            return results

        return process_complex_filter(filters)

    @staticmethod
    def parse_native_hybrid_results(
        result_json_str: str,
        primary_field: str = "id",
        text_field: str = "document",
        metadata_field: str = "metadata"
    ) -> List[Dict]:
        """
        Parse the JSON results from OceanBase native DBMS_HYBRID_SEARCH.SEARCH.

        Args:
            result_json_str: JSON string returned from DBMS_HYBRID_SEARCH.SEARCH
            primary_field: Name of the primary key field
            text_field: Name of the text content field
            metadata_field: Name of the metadata field

        Returns:
            List[Dict]: List of parsed result dictionaries, each containing:
                - vector_id: Primary key value
                - text_content: Text content
                - score: Relevance score from _score field
                - user_id, agent_id, run_id, actor_id: Standard ID fields
                - hash, created_at, updated_at, category: Standard fields
                - metadata_json: Raw metadata JSON
        """
        try:
            result_data = json.loads(result_json_str)

            if not isinstance(result_data, list):
                logger.warning(f"Unexpected result format: {type(result_data)}, expected list")
                return []

            output_list = []

            for doc in result_data:
                vector_id = doc.get(primary_field)
                score = doc.get("_score", 0.0)

                if not vector_id:
                    logger.warning(f"Document missing primary key '{primary_field}', skipping")
                    continue

                output_list.append({
                    "vector_id": vector_id,
                    "text_content": doc.get(text_field, ""),
                    "score": score,
                    "user_id": doc.get("user_id", ""),
                    "agent_id": doc.get("agent_id", ""),
                    "run_id": doc.get("run_id", ""),
                    "actor_id": doc.get("actor_id", ""),
                    "hash": doc.get("hash", ""),
                    "created_at": doc.get("created_at", ""),
                    "updated_at": doc.get("updated_at", ""),
                    "category": doc.get("category", ""),
                    "metadata_json": doc.get(metadata_field, {}),
                })

            logger.debug(f"Parsed {len(output_list)} results from native hybrid search")
            return output_list

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse native hybrid search JSON results: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing native hybrid search results: {e}")
            return []