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
    from pyobvector import FtsParser
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