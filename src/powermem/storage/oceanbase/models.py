"""
OceanBase ORM Model Definitions

This module defines SQLAlchemy ORM models for OceanBase storage.
Supports dynamic table names and vector dimension configuration.
"""
from typing import Optional, Type

from sqlalchemy import BigInteger, String, JSON, Column
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.mysql import LONGTEXT

try:
    from pyobvector import VECTOR, SPARSE_VECTOR
except ImportError:
    raise ImportError(
        "pyobvector is required for ORM models. "
        "Please install it: pip install pyobvector"
    )

# Create declarative base class
Base = declarative_base()

# Cache dynamically created model classes
_model_cache = {}


def create_memory_model(
    table_name: str,
    embedding_dims: int,
    include_sparse: bool = True,
    primary_field: str = "id",
    vector_field: str = "embedding",
    text_field: str = "document",
    metadata_field: str = "metadata",
    fulltext_field: str = "fulltext_content",
    sparse_vector_field: str = "sparse_embedding"
) -> Type[Base]:
    """
    Dynamically create Memory model class.
    
    Create a dedicated ORM model based on the provided table name and vector dimension.
    Models are cached to avoid duplicate creation.
    
    Args:
        table_name: Table name
        embedding_dims: Vector dimension
        include_sparse: Whether to include sparse vector column
        primary_field: Primary key field name (default: "id")
        vector_field: Vector field name (default: "embedding")
        text_field: Text field name (default: "document")
        metadata_field: Metadata field name (default: "metadata")
        fulltext_field: Full-text search field name (default: "fulltext_content")
        sparse_vector_field: Sparse vector field name (default: "sparse_embedding")
    
    Returns:
        Configured model class
    """
    cache_key = (table_name, embedding_dims, include_sparse, primary_field, 
                 vector_field, text_field, metadata_field, fulltext_field, sparse_vector_field)
    
    if cache_key in _model_cache:
        return _model_cache[cache_key]
    
    # Dynamically create class name
    class_name = f"MemoryRecord_{table_name}_{embedding_dims}"
    
    # Define columns
    columns = {
        '__tablename__': table_name,
        '__table_args__': {'extend_existing': True},
        
        # Primary key - Use Snowflake ID (BIGINT without AUTO_INCREMENT)
        primary_field: Column(BigInteger, primary_key=True, autoincrement=False),
        
        # Vector field
        vector_field: Column(VECTOR(embedding_dims), nullable=False),
        
        # Text field
        text_field: Column(LONGTEXT, nullable=False),
        
        # Identifier fields
        'user_id': Column(String(128), nullable=True),
        'agent_id': Column(String(128), nullable=True),
        'run_id': Column(String(128), nullable=True),
        'actor_id': Column(String(128), nullable=True),
        
        # Other fields
        'hash': Column(String(32), nullable=True),
        'created_at': Column(String(128), nullable=True),
        'updated_at': Column(String(128), nullable=True),
        'category': Column(String(64), nullable=True),
        
        # Full-text search field
        fulltext_field: Column(LONGTEXT, nullable=True),
        
        # Add __repr__ method
        '__repr__': lambda self: f"<{class_name}(id={getattr(self, primary_field)}, user_id={self.user_id}, agent_id={self.agent_id})>"
    }
    
    # Add metadata field (JSON)
    # If metadata_field is 'metadata', conflicts with SQLAlchemy reserved word, use attribute mapping
    if metadata_field == 'metadata':
        columns['metadata_'] = Column('metadata', JSON, nullable=True)
    else:
        columns[metadata_field] = Column(JSON, nullable=True)
    
    # Add sparse vector column (if enabled)
    if include_sparse:
        columns[sparse_vector_field] = Column(SPARSE_VECTOR, nullable=True)
    
    # Dynamically create class and inherit from Base
    model_class = type(class_name, (Base,), columns)
    
    # Cache model class
    _model_cache[cache_key] = model_class
    
    return model_class

