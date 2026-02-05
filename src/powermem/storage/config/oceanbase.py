from typing import Any, ClassVar, Dict, Optional

from pydantic import AliasChoices, Field, model_validator
from powermem.settings import settings_config

from powermem.storage.config.base import BaseVectorStoreConfig, BaseGraphStoreConfig


class OceanBaseConfig(BaseVectorStoreConfig):
    _provider_name = "oceanbase"
    _class_path = "powermem.storage.oceanbase.oceanbase.OceanBaseVectorStore"
    
    try:
        from pyobvector import ObVecClient
    except ImportError:
        raise ImportError("The 'pyobvector' library is required. Please install it using 'pip install pyobvector'.")
    ObVecClient: ClassVar[type] = ObVecClient

    model_config = settings_config("VECTOR_STORE_", extra="forbid", env_file=None)

    collection_name: str = Field(
        default="power_mem",
        validation_alias=AliasChoices(
            "collection_name",
            "VECTOR_STORE_COLLECTION_NAME",
            "OCEANBASE_COLLECTION",
        ),
        description="Default name for the collection"
    )

    # Connection parameters
    host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices(
            "host",
            "OCEANBASE_HOST",
        ),
        description="OceanBase server host"
    )
    
    port: str = Field(
        default="2881",
        validation_alias=AliasChoices(
            "port",
            "OCEANBASE_PORT",
        ),
        description="OceanBase server port"
    )
    
    user: str = Field(
        default="root@test",
        validation_alias=AliasChoices(
            "OCEANBASE_USER",
            "user", # avoid using system USER environment variable first
        ),
        description="OceanBase username"
    )
    
    password: str = Field(
        default="",
        validation_alias=AliasChoices(
            "password",
            "OCEANBASE_PASSWORD",
        ),
        description="OceanBase password"
    )
    
    db_name: str = Field(
        default="test",
        validation_alias=AliasChoices(
            "db_name",
            "OCEANBASE_DATABASE",
        ),
        description="OceanBase database name"
    )

    connection_args: Optional[dict] = Field(
        default=None,
        validation_alias=AliasChoices(
            "connection_args",
        ),
        description="OceanBase connection args"
    )

    # Vector index parameters
    index_type: str = Field(
        default="HNSW",
        validation_alias=AliasChoices(
            "index_type",
            "OCEANBASE_INDEX_TYPE",
        ),
        description="Type of vector index (HNSW, IVF, FLAT, etc.)"
    )
    
    vidx_metric_type: str = Field(
        default="l2",
        validation_alias=AliasChoices(
            "vidx_metric_type",
            "OCEANBASE_VECTOR_METRIC_TYPE",
        ),
        description="Distance metric (l2, inner_product, cosine)"
    )
    
    embedding_model_dims: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices(
            "embedding_model_dims",
            "OCEANBASE_EMBEDDING_MODEL_DIMS",
        ),
        description="Dimension of vectors"
    )

    # Advanced parameters
    vidx_algo_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Index algorithm parameters"
    )
    
    normalize: bool = Field(
        default=False,
        description="Whether to normalize vectors"
    )
    
    include_sparse: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "include_sparse",
            "OCEANBASE_INCLUDE_SPARSE",
            "SPARSE_VECTOR_ENABLE",
        ),
        description="Whether to include sparse vector support"
    )
    
    hybrid_search: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "hybrid_search",
        ),
        description="Whether to enable hybrid search"
    )

    enable_native_hybrid: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "enable_native_hybrid",
            "OCEANBASE_ENABLE_NATIVE_HYBRID",
        ),
        description="Whether to enable OceanBase native hybrid search"
    )
    
    auto_configure_vector_index: bool = Field(
        default=True,
        description="Whether to automatically configure vector index settings"
    )

    # Fulltext search parameters
    fulltext_parser: str = Field(
        default="ik",
        description="Fulltext parser type (ik, ngram, ngram2, beng, space)"
    )

    # Field names
    primary_field: str = Field(
        default="id",
        validation_alias=AliasChoices(
            "primary_field",
            "OCEANBASE_PRIMARY_FIELD",
        ),
        description="Primary key field name"
    )
    
    vector_field: str = Field(
        default="embedding",
        validation_alias=AliasChoices(
            "vector_field",
            "OCEANBASE_VECTOR_FIELD",
        ),
        description="Vector field name"
    )
    
    text_field: str = Field(
        default="document",
        validation_alias=AliasChoices(
            "text_field",
            "OCEANBASE_TEXT_FIELD",
        ),
        description="Text field name"
    )
    
    metadata_field: str = Field(
        default="metadata",
        validation_alias=AliasChoices(
            "metadata_field",
            "OCEANBASE_METADATA_FIELD",
        ),
        description="Metadata field name"
    )
    
    vidx_name: str = Field(
        default="vidx",
        validation_alias=AliasChoices(
            "vidx_name",
            "OCEANBASE_VIDX_NAME",
        ),
        description="Vector index name"
    )

    vector_weight: float = Field(
        default=0.5,
        description="Weight for vector search"
    )
    
    fts_weight: float = Field(
        default=0.5,
        description="Weight for fulltext search"
    )
    
    sparse_weight: Optional[float] = Field(
        default=None,
        description="Weight for sparse vector search"
    )



class OceanBaseGraphConfig(BaseGraphStoreConfig):
    """Configuration for OceanBase graph store."""
    
    _provider_name = "oceanbase"
    _class_path = "powermem.storage.oceanbase.oceanbase_graph.MemoryGraph"
    
    model_config = settings_config("GRAPH_STORE_", extra="forbid", env_file=None)
    
    # All fields (connection, vector, max_hops) are inherited from BaseGraphStoreConfig
    # No additional fields needed for OceanBase GraphStore at this time
