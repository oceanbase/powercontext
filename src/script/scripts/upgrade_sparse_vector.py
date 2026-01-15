"""
Sparse Vector Upgrade Script

This script is used to add sparse vector support to existing OceanBase tables:
- Add sparse_embedding column (SPARSE_VECTOR type)
- Create sparse_embedding_idx index
"""
import logging
from typing import Dict, Any, Tuple

from pyobvector import SPARSE_VECTOR, IndexParam, ObVecClient
from sqlalchemy import text, Column

logger = logging.getLogger(__name__)


def _validate_and_parse_config(config: Dict[str, Any]) -> Tuple[ObVecClient, str]:
    """
    Validate configuration and create database connection
    
    Args:
        config: PowerMem configuration dictionary
        
    Returns:
        Tuple[ObVecClient, str]: (database connection object, table name)
        
    Raises:
        TypeError: Invalid configuration type
        ValueError: Invalid configuration content
        RuntimeError: Connection failed
    """
    from powermem.configs import MemoryConfig
    
    # 1. Validate config type
    if not isinstance(config, (dict, MemoryConfig)):
        raise TypeError(
            f"Expected dict or MemoryConfig instance, got {type(config).__name__}. "
            f"Please pass a config dict:\n"
            f"  from powermem import auto_config\n"
            f"  config = auto_config()\n"
            f"  ScriptManager.run('upgrade-sparse-vector', config)"
        )
    
    # 2. Convert to dict if needed
    config_dict = config.model_dump() if isinstance(config, MemoryConfig) else config
    
    # 3. Validate vector_store config
    vector_store_config = config_dict.get('vector_store')
    if not vector_store_config:
        raise ValueError(
            "No vector_store configuration found in config. "
            "Please ensure config contains 'vector_store' section."
        )
    
    # 4. Validate storage type
    storage_type = vector_store_config.get('provider', '').lower()
    if storage_type != 'oceanbase':
        raise ValueError(
            f"Vector store type is not OceanBase (got '{storage_type}'). "
            f"This upgrade script only supports OceanBase storage. "
            f"Please configure vector_store.provider as 'oceanbase'."
        )
    
    # 5. Extract connection parameters
    ob_config = vector_store_config.get('config', {})
    connection_args = ob_config.get('connection_args', {})
    host = connection_args.get('host')
    port = connection_args.get('port')
    user = connection_args.get('user')
    password = connection_args.get('password')
    db_name = connection_args.get('db_name')
    collection_name = ob_config.get('collection_name', 'power_mem')
    
    # 6. Validate required parameters
    if not all([host, port, user, db_name]):
        missing = []
        if not host: missing.append('host')
        if not port: missing.append('port')
        if not user: missing.append('user')
        if not db_name: missing.append('db_name')
        raise ValueError(
            f"Missing required OceanBase connection parameters: {', '.join(missing)}. "
            f"Please ensure config contains 'vector_store.config.connection_args.{{host, port, user, db_name}}'."
        )
    
    # 7. Create database connection
    try:
        logger.info(f"Connecting to OceanBase at {host}:{port}...")
        obvector = ObVecClient(
            uri=f"{host}:{port}",
            user=user,
            password=password or "",
            db_name=db_name
        )
        logger.info(f"Connected successfully to database '{db_name}'")
        return obvector, collection_name
    except Exception as e:
        raise RuntimeError(f"Failed to connect to OceanBase: {e}") from e


def upgrade_sparse_vector(config: Dict[str, Any]) -> bool:
    """
    Add sparse vector support to OceanBase table
    
    This function checks and adds the columns and indexes required for sparse vectors.
    It is idempotent and can be safely called multiple times
    (existing columns and indexes will be skipped).
    
    Args:
        config: PowerMem configuration dictionary containing OceanBase connection settings
        
    Returns:
        bool: Returns True on success, False on failure
        
    Raises:
        TypeError: Invalid configuration type
        ValueError: Storage type is not OceanBase or config is invalid
        RuntimeError: Database version does not support sparse vectors
    """
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    # Validate config and create connection
    obvector, collection_name = _validate_and_parse_config(config)
    
    logger.info(f"Starting sparse vector upgrade for table '{collection_name}'")
    
    # Check if database version supports sparse vectors
    if not OceanBaseUtil.check_sparse_vector_version_support(obvector):
        raise RuntimeError(
            "Database version does not support SPARSE_VECTOR type. "
            "Sparse vector requires seekdb or OceanBase >= 4.5.0. "
            "Please upgrade your database before running this script."
        )
    
    # Check if table exists
    if not OceanBaseUtil.check_table_exists(obvector, collection_name):
        raise RuntimeError(
            f"Table '{collection_name}' does not exist. "
            f"Please create the table first by initializing Memory with include_sparse=False, "
            f"then run this upgrade script."
        )
    
    try:
        # Add sparse_embedding column (if not exists)
        _add_sparse_embedding_column(obvector, collection_name)
        
        # Create sparse_embedding index (if not exists)
        _create_sparse_embedding_index(obvector, collection_name)
        
        logger.info(f"Sparse vector upgrade completed successfully for table '{collection_name}'")
        return True
        
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Sparse vector upgrade failed: {e}", exc_info=True)
        raise RuntimeError(f"Sparse vector upgrade failed: {e}") from e


def _add_sparse_embedding_column(obvector: ObVecClient, table_name: str) -> None:
    """Add sparse_embedding column to table"""
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    if OceanBaseUtil.check_column_exists(obvector, table_name, 'sparse_embedding'):
        logger.info("sparse_embedding column already exists, skipping")
        return
    
    logger.info(f"Adding sparse_embedding column to table '{table_name}'")
    try:
        obvector.add_columns(
            table_name=table_name,
            columns=[Column('sparse_embedding', SPARSE_VECTOR)]
        )
        logger.info("sparse_embedding column added successfully")
    except Exception as e:
        error_str = str(e).lower()
        if '1060' in str(e) or 'already exists' in error_str or 'duplicate column' in error_str:
            logger.info("sparse_embedding column already exists (race condition), skipping")
        else:
            raise RuntimeError(f"Failed to add sparse_embedding column: {e}") from e


def _create_sparse_embedding_index(obvector: ObVecClient, table_name: str) -> None:
    """Create sparse vector index on table"""
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    if OceanBaseUtil.check_index_exists(obvector, table_name, 'sparse_embedding_idx'):
        logger.info("sparse_embedding_idx already exists, skipping")
        return
    
    logger.info(f"Creating sparse vector index on table '{table_name}'")
    try:
        vidx_param = IndexParam(
            field_name="sparse_embedding",
            index_type="daat",
            index_name="sparse_embedding_idx",
            metric_type="inner_product",
            sparse_index_type="sindi",
        )
        
        obvector.create_vidx_with_vec_index_param(
            table_name=table_name,
            vidx_param=vidx_param,
        )
        logger.info("sparse_embedding_idx created successfully")
    except Exception as e:
        error_str = str(e).lower()
        if '1061' in str(e) or 'already exists' in error_str or 'duplicate key' in error_str:
            logger.info("sparse_embedding_idx already exists (race condition), skipping")
        else:
            raise RuntimeError(f"Failed to create sparse_embedding_idx: {e}") from e


def downgrade_sparse_vector(config: Dict[str, Any]) -> bool:
    """
    Remove sparse vector support from OceanBase table (rollback operation)
    
    This function removes sparse vector columns and indexes.
    
    Warning: This operation will delete all sparse vector data and is irreversible!
    
    Args:
        config: PowerMem configuration dictionary containing OceanBase connection settings
        
    Returns:
        bool: Returns True on success, False on failure
        
    Raises:
        TypeError: Invalid configuration type
        ValueError: Storage type is not OceanBase or config is invalid
    """
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    # Validate config and create connection
    obvector, collection_name = _validate_and_parse_config(config)
    
    logger.info(f"Starting sparse vector downgrade for table '{collection_name}'")
    
    # Check if table exists
    if not OceanBaseUtil.check_table_exists(obvector, collection_name):
        logger.info(f"Table '{collection_name}' does not exist, nothing to downgrade")
        return True
    
    try:
        # Drop sparse_embedding index (if exists)
        _drop_sparse_embedding_index(obvector, collection_name)
        
        # Drop sparse_embedding column (if exists)
        _drop_sparse_embedding_column(obvector, collection_name)
        
        logger.info(f"Sparse vector downgrade completed successfully for table '{collection_name}'")
        return True
        
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Sparse vector downgrade failed: {e}", exc_info=True)
        raise RuntimeError(f"Sparse vector downgrade failed: {e}") from e


def _drop_sparse_embedding_index(obvector: ObVecClient, table_name: str) -> None:
    """Drop sparse vector index from table"""
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    if not OceanBaseUtil.check_index_exists(obvector, table_name, 'sparse_embedding_idx'):
        logger.info("sparse_embedding_idx does not exist, skipping")
        return
    
    logger.info(f"Dropping sparse vector index from table '{table_name}'")
    try:
        with obvector.engine.connect() as conn:
            conn.execute(text(f"DROP INDEX sparse_embedding_idx ON {table_name}"))
            conn.commit()
        logger.info("sparse_embedding_idx dropped successfully")
    except Exception as e:
        error_str = str(e).lower()
        if '1091' in str(e) or "doesn't exist" in error_str:
            logger.info("sparse_embedding_idx does not exist (race condition), skipping")
        else:
            raise RuntimeError(f"Failed to drop sparse_embedding_idx: {e}") from e


def _drop_sparse_embedding_column(obvector: ObVecClient, table_name: str) -> None:
    """Drop sparse_embedding column from table"""
    from powermem.utils.oceanbase_util import OceanBaseUtil
    
    if not OceanBaseUtil.check_column_exists(obvector, table_name, 'sparse_embedding'):
        logger.info("sparse_embedding column does not exist, skipping")
        return
    
    logger.info(f"Dropping sparse_embedding column from table '{table_name}'")
    try:
        with obvector.engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} DROP COLUMN sparse_embedding"))
            conn.commit()
        logger.info("sparse_embedding column dropped successfully")
    except Exception as e:
        error_str = str(e).lower()
        if '1091' in str(e) or "doesn't exist" in error_str:
            logger.info("sparse_embedding column does not exist (race condition), skipping")
        else:
            raise RuntimeError(f"Failed to drop sparse_embedding column: {e}") from e
